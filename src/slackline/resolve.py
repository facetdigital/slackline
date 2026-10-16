"""Selector to conversation, deterministically or not at all (D6, D8).

The riskiest part of the whole tool is not retrieval. It is the phrase "the DM
with Joan and Kim", which can mean a three-person group DM, or two separate
one-to-one DMs, or a channel that happens to contain people by those names.

Two rules govern everything here.

**Ambiguity is a hard failure.** Never a best match, never first-hit-wins, never
a warning that scrolls past. The reasoning is about the future more than the
present: a read tool that guesses wrong wastes tokens, but the same resolver
behind ``slackline-write post`` that guesses wrong sends a message to the wrong
people. Building the strict habit while the stakes are low is the only way it is
still strict when they are not.

**Group DMs are verified by membership, not by name.** Slack names them
``mpdm-<handle>--<handle>-N``, and those names are *not unique*: two distinct
conversation IDs in the live workspace share one. The tidy solution,
``conversations.open`` with ``prevent_creation``, is banned because that method
can create a conversation, and this is a read-only tool.

Three facts about those names, all established against the live workspace rather
than assumed, and all of which shaped this code:

1. ``conversations.info`` does **not** return a ``members`` key for group DMs.
   Membership comes from ``conversations.members``, which is a separate call.
2. **Names go stale.** The colliding pair is named ``...--lee--...``, but
   that account is now ``deactivateduser000001``. Filtering candidates by current
   handles alone would therefore exclude the very conversation being asked for.
   The name is a fast path, never the authority, and there is a fallback scan
   for when it misses.
3. **Membership does not always disambiguate.** The two colliding conversations
   have *identical* member sets. Verification is necessary for correctness but
   is not sufficient to separate every collision, so a genuine tie is reported as
   ambiguous with both IDs rather than resolved by picking one.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from .errors import AmbiguousSelector, ArgumentError, NotFound


def check_selectors(channels, with_users, conversations) -> None:
    """Exactly one kind of selector, or an ArgumentError.

    Shared with ``commands._common.validate_selection`` so the message a
    command gives before any credential is resolved is the same one the
    resolver gives; the two cannot drift.
    """
    supplied = [
        name for name, value in
        (("--channel", channels), ("--with", with_users), ("--conversation", conversations))
        if value
    ]
    if not supplied:
        raise ArgumentError(
            "no conversation selected. Use --channel NAME, --with PERSON "
            "(repeatable), or --conversation ID."
        )
    if len(supplied) > 1:
        raise ArgumentError(
            "conflicting selectors: " + ", ".join(supplied) +
            ". Use exactly one kind per invocation, so what is being read stays "
            "obvious."
        )


class Resolver:
    #: How many membership probes the fallback scan may make before giving up
    #: and telling the caller to select by ID. A bounded amount of extra work is
    #: worth it for a correct answer; an unbounded amount is a hang.
    MAX_MEMBER_PROBES = 60

    def __init__(self, client, directory, self_user_id: str, warn=None, cache=None):
        self.client = client
        self.directory = directory
        self.self_user_id = self_user_id
        self.warn = warn or (lambda _message: None)
        self.cache = cache
        # Persisted across invocations. The fallback scan can cost 60 sequential
        # API calls, measured at about 9 seconds against the live workspace, and
        # agents call these tools in loops. Group DM membership is immutable in
        # Slack, so this is safe to keep.
        self._member_cache: Dict[str, List[str]] = {}
        if self.cache is not None:
            from .cache import MEMBERS_TTL

            self._member_cache = dict(self.cache.get("members", MEMBERS_TTL) or {})
        self._member_cache_dirty = False

    def save_member_cache(self) -> None:
        if self.cache is not None and self._member_cache_dirty:
            try:
                self.cache.put("members", self._member_cache)
            except OSError:
                pass  # a cache we cannot write is a slow run, not a failed one
            self._member_cache_dirty = False

    # -- users --------------------------------------------------------------

    def resolve_user(self, selector: str) -> Dict[str, Any]:
        matches = self.directory.find_users(selector)
        if not matches:
            raise NotFound(
                f"no user matches {selector!r}. Try 'slackline-read users --match "
                f"{selector.lstrip('@')}' to see what is available."
            )
        if len(matches) > 1:
            raise AmbiguousSelector(
                f"ambiguous user selector {selector!r}. Pass one @handle from "
                "the candidates to pick one person.",
                candidates=[
                    f"{u['id']}  {u['real_name'] or u['display']} (@{u['handle']})"
                    for u in sorted(matches, key=lambda u: u["handle"])
                ],
            )
        return matches[0]

    # -- conversations ------------------------------------------------------

    def _members_of(self, conversation_id: str) -> List[str]:
        """The real member list, from the only method that actually returns one.

        ``conversations.info`` returns no ``members`` key for group DMs, verified
        against the live workspace. Results are memoized because the resolver may
        probe several candidates and agents call these tools in loops; group DM
        membership is immutable in Slack, so caching it is safe.
        """
        if conversation_id in self._member_cache:
            return self._member_cache[conversation_id]

        members: List[str] = []
        for page in self.client.paginate(
            "conversations.members", "members", channel=conversation_id, limit=200
        ):
            members.extend(page.items)
        self._member_cache[conversation_id] = members
        self._member_cache_dirty = True
        return members

    def _probe(self, conversation: Dict[str, Any]) -> Optional[List[str]]:
        """The verified members of one candidate, or None if Slack cannot say.

        A stale or archived candidate can answer channel_not_found. That means
        "not this one", not that a resolution a live row satisfies should fail.
        """
        try:
            return self._members_of(conversation["id"])
        except NotFound as exc:
            self.warn(f"skipping {conversation['id']} ({conversation['label']}): {exc}")
            return None

    def _confirm(
        self, candidates: Sequence[Dict[str, Any]], expected: set, names: str, by: str
    ) -> List[Dict[str, Any]]:
        """Probe candidates, live first, and keep the ones with exactly ``expected``.

        Once a live conversation has confirmed, archived candidates are not
        probed: they would lose the preference anyway, and the budget belongs
        to conversations the caller can plausibly mean. Whatever was set aside
        is announced, so the preference is never silent (D8).
        """
        confirmed: List[Dict[str, Any]] = []
        set_aside: List[Dict[str, Any]] = []
        for candidate in candidates:
            if candidate["archived"] and any(not c["archived"] for c in confirmed):
                set_aside.append(candidate)
                continue
            members = self._probe(candidate)
            if members is not None and set(members) == expected:
                confirmed.append(candidate)
        live = [c for c in confirmed if not c["archived"]]
        if live:
            set_aside.extend(c for c in confirmed if c["archived"])
        if live and set_aside:
            # Worded by how the candidates were found: on the membership scan
            # the name did not match, by definition.
            how = "a matching name" if by == "name" else "these members"
            self.warn(
                f"{names}: archived group DM"
                f"{'' if len(set_aside) == 1 else 's'} with {how} "
                f"({', '.join(c['id'] for c in set_aside)}) set aside; a live one "
                f"takes precedence. Pass --conversation ID to read an archived one."
            )
        return live or confirmed

    def _resolve_with(self, selectors: Sequence[str]) -> List[Dict[str, Any]]:
        users = [self.resolve_user(selector) for selector in selectors]
        wanted = {user["id"] for user in users}
        wanted.discard(self.self_user_id)

        if not wanted:
            raise ArgumentError("--with needs at least one person other than yourself")

        # A single other person means the one-to-one DM, which is unambiguous:
        # Slack allows exactly one IM per pair.
        if len(wanted) == 1:
            other = next(iter(wanted))
            matches = [
                c for c in self.directory.conversations()
                if c["kind"] == "im" and other in (c["members"] or [])
            ]
            if not matches:
                handle = self.directory.handle_for(other)
                raise NotFound(f"no direct message with {handle} is visible to you")
            return matches[:1]

        expected = wanted | {self.self_user_id}

        # Narrow by parsed name first. This is cheap and takes 215 group DMs
        # down to a handful, so the verification pass below stays to one or two
        # API calls rather than one per group DM in the workspace.
        wanted_handles = {self.directory.user(uid)["handle"] for uid in wanted
                          if self.directory.user(uid)}
        group_dms = [c for c in self.directory.conversations() if c["kind"] == "mpim"]
        names = ", ".join(sorted("@" + h for h in wanted_handles))

        # Live before archived, here and in the fallback below. Slack creates
        # a new group DM rather than reviving an archived one with the same
        # people, so a live/archived pair with identical members is the
        # ordinary shape, not a tie; and the probe budget should be spent on
        # conversations a caller can plausibly mean (D8, archived namesakes).
        candidates = sorted(
            (c for c in group_dms
             if wanted_handles.issubset(set(c["member_handles"]))
             and len(c["member_handles"]) == len(expected)),
            key=lambda c: c["archived"],
        )
        confirmed = self._confirm(candidates, expected, names, by="name")

        if not confirmed:
            # The name pre-filter can miss, because names record the handles
            # people had when the conversation was created. A deactivated
            # colleague is renamed to something like 'deactivateduser000001',
            # and the old handle lives on in the name forever. Falling back to a
            # membership scan is the only way to find those.
            fallback = sorted(
                (c for c in group_dms
                 if c not in candidates and len(c["member_handles"]) == len(expected)),
                key=lambda c: c["archived"],
            )
            if fallback:
                self.warn(
                    f"no group DM matched {names} by name, scanning membership of "
                    f"{min(len(fallback), self.MAX_MEMBER_PROBES)} candidates "
                    f"(a group DM name keeps the handles people had when it was "
                    f"created, so a renamed or deactivated account will not match)"
                )
            confirmed = self._confirm(fallback[: self.MAX_MEMBER_PROBES], expected, names,
                                      by="members")
            if not confirmed and len(fallback) > self.MAX_MEMBER_PROBES:
                raise NotFound(
                    f"no group direct message with exactly {names} (and you) found "
                    f"within {self.MAX_MEMBER_PROBES} candidates. Select it by ID: "
                    f"'slackline-read channels --kind mpim' lists them."
                )

        if not confirmed:
            raise NotFound(f"no group direct message whose members are exactly {names} (and you)")
        if len(confirmed) > 1:
            raise AmbiguousSelector(
                "several group direct messages have exactly these members, so "
                "membership cannot separate them. This is real: two conversations "
                "in this workspace share both a name and a member set. Select one "
                "by ID.",
                candidates=[
                    f"{c['id']}  {c['label']}  (name: {c['name']})  members: "
                    + ", ".join(sorted(self.directory.handle_for(u)
                                       for u in self._members_of(c["id"])))
                    for c in confirmed
                ],
            )
        # The members a caller sees are the verified set, not the handles parsed
        # from the name. For a conversation found by the fallback scan those two
        # differ, and the name-derived list would be wrong in exactly the case
        # verification was needed for (D6). The set is already memoized, so
        # this costs no call.
        return [dict(c, members=sorted(self._members_of(c["id"]))) for c in confirmed]

    def _resolve_channel(self, selector: str) -> List[Dict[str, Any]]:
        matches = self.directory.find_conversations_by_name(selector)
        if matches and not any(m["archived"] for m in matches):
            # The directory prefers live rows over archived namesakes rather
            # than reporting a tie, which is a deliberate carve-out from D8.
            # It is not silent: the caller hears what was set aside and how to
            # read it if that is what they meant.
            namesakes = self.directory.find_archived_namesakes(selector)
            if namesakes:
                self.warn(
                    f"{selector}: also matches {len(namesakes)} archived conversation"
                    f"{'' if len(namesakes) == 1 else 's'} ("
                    + ", ".join(c["id"] for c in namesakes)
                    + "), which the live one takes precedence over. Pass "
                    f"--conversation ID to read an archived one."
                )
        if not matches:
            raise NotFound(
                f"no conversation matches {selector!r}. Try 'slackline-read channels "
                f"--match {selector.lstrip('#')}'."
            )
        if len(matches) > 1:
            raise AmbiguousSelector(
                f"ambiguous conversation selector {selector!r}",
                candidates=[f"{c['id']}  {c['kind']}  {c['label']}" for c in matches],
            )
        return matches

    # -- entry point --------------------------------------------------------

    def resolve(
        self,
        channels: Optional[Sequence[str]] = None,
        with_users: Optional[Sequence[str]] = None,
        conversations: Optional[Sequence[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Resolve exactly one kind of selector into conversation records."""
        check_selectors(channels, with_users, conversations)

        if conversations:
            # An explicit ID is already unambiguous. Second-guessing it would
            # only add ways to be wrong.
            resolved = []
            for conversation_id in conversations:
                known = self.directory.conversation(conversation_id)
                resolved.append(
                    known or {"id": conversation_id, "kind": "unknown",
                              "name": "", "label": conversation_id,
                              "members": [], "member_handles": []}
                )
            return resolved

        if channels:
            found: List[Dict[str, Any]] = []
            for selector in channels:
                found.extend(self._resolve_channel(selector))
            return found

        try:
            return self._resolve_with(list(with_users or []))
        finally:
            # Save even when resolution failed or was ambiguous: the membership
            # we learned is valid regardless of the outcome, and the next
            # invocation should not repeat the scan.
            self.save_member_cache()
