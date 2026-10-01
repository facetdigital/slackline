"""The user and conversation directory: who exists, and what can be read.

Built from ``users.list`` and ``users.conversations``, cached on disk (D7).

``users.conversations`` rather than ``conversations.list`` (D5). It enumerates
what *this user* is in, which is exactly the authority boundary Slackline
operates within: smaller, faster, and structurally incapable of returning
something the user cannot read.

``users.list`` returns only this workspace. People on the other side of a
channel shared with another company (Slack Connect) are not in it, so a second,
smaller index holds users found outside it: learned from the ``user_profile``
Slack attaches to their messages, else from a targeted ``users.info`` (D7).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .cache import CONVERSATIONS_TTL, EXTERNAL_USERS_TTL, USERS_TTL
from .errors import SlacklineError

# Slack names group DMs 'mpdm-<handle>--<handle>--<handle>-<n>'. All 215 group
# DMs in the surveyed workspace matched. The separator is a DOUBLE hyphen, which
# is what lets a handle like 'joan-w' survive the split intact.
_MPIM_RE = re.compile(r"^mpdm-(?P<body>.+?)-(?P<serial>\d+)$")

# User IDs start with U, or W on Enterprise Grid. Anything else is not worth a
# users.info call, and an empty or malformed mention must not become one.
_USER_ID_RE = re.compile(r"^[UW][A-Z0-9]+$")


def parse_mpim_name(name: str) -> List[str]:
    """Return the member handles encoded in a group DM name, or []."""
    match = _MPIM_RE.match(name or "")
    if not match:
        return []
    return [part for part in match.group("body").split("--") if part]


def classify(conversation: Dict[str, Any]) -> str:
    if conversation.get("is_im"):
        return "im"
    if conversation.get("is_mpim"):
        return "mpim"
    if conversation.get("is_private"):
        return "private"
    return "public"


class Directory:
    """Lazily built, disk-cached index of users and conversations."""

    def __init__(self, client, cache, self_user_id: str):
        self.client = client
        self.cache = cache
        self.self_user_id = self_user_id
        self._users: Optional[Dict[str, Dict[str, Any]]] = None
        self._conversations: Optional[List[Dict[str, Any]]] = None
        # Users found outside users.list, and IDs users.info could not name.
        # The second set lives for one process only: it exists so a transcript
        # that mentions an unknown ID fifty times asks Slack once, not fifty.
        self._outside: Optional[Dict[str, Dict[str, Any]]] = None
        self._unresolvable: set = set()

    # -- users --------------------------------------------------------------

    def users(self) -> Dict[str, Dict[str, Any]]:
        if self._users is not None:
            return self._users

        cached = self.cache.get("users", USERS_TTL)
        if cached is not None:
            self._users = cached
            return cached

        index: Dict[str, Dict[str, Any]] = {}
        for page in self.client.paginate("users.list", "members", limit=200):
            for member in page.items:
                profile = member.get("profile") or {}
                real_name = profile.get("real_name") or member.get("real_name") or ""
                index[member["id"]] = {
                    "id": member["id"],
                    "handle": member.get("name") or "",
                    "real_name": real_name,
                    # Slack leaves display_name empty far more often than people
                    # expect, so fall back rather than rendering a blank name.
                    "display": profile.get("display_name") or real_name or member.get("name") or "",
                    "email": profile.get("email") or None,
                    "is_bot": bool(member.get("is_bot")),
                    "deleted": bool(member.get("deleted")),
                }
        self._users = index
        self.cache.put("users", index)
        return index

    def user(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Whoever is already known by this ID. Never calls Slack."""
        return self.users().get(user_id) or self._outside_users().get(user_id)

    def identify(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Whoever this ID is, asking Slack with ``users.info`` if need be.

        For an ID neither index holds: an external user nobody here has heard
        from yet, or a colleague who joined after the directory was cached. Any
        failure is a None, remembered for the process, never an exception: an
        unresolvable user must not break a transcript.
        """
        record = self.user(user_id)
        if record is not None or not _USER_ID_RE.match(user_id or ""):
            return record
        if user_id in self._unresolvable:
            return None
        try:
            member = self.client.call("users.info", user=user_id).get("user") or {}
        except SlacklineError:
            member = {}
        if member.get("id") != user_id:
            self._unresolvable.add(user_id)
            return None
        profile = member.get("profile") or {}
        record = self._outsider(
            user_id,
            handle=member.get("name") or profile.get("name") or "",
            profile=profile,
            team=member.get("team_id") or profile.get("team"),
            is_bot=bool(member.get("is_bot")),
            deleted=bool(member.get("deleted")),
        )
        self._remember(record)
        return record

    def learn_from_message(self, message: Dict[str, Any]) -> None:
        """Take a sender's identity from the message itself, when Slack sent one.

        Slack attaches a ``user_profile`` to a message from another workspace in
        a shared channel. It is the cheapest and freshest answer to "who said
        this", so it is preferred to ``users.info``, and it overwrites what was
        cached when it differs. Our own people come from ``users.list`` and are
        left alone.
        """
        user_id = message.get("user") or ""
        profile = message.get("user_profile")
        if not user_id or not isinstance(profile, dict) or user_id in self.users():
            return
        self._remember(self._outsider(
            user_id,
            handle=profile.get("name") or "",
            profile=profile,
            team=message.get("user_team") or profile.get("team"),
        ))

    def handle_for(self, user_id: str) -> str:
        """A renderable name for an ID, never an exception.

        An external user renders by name, marked ``(ext)``: their handle belongs
        to another workspace and is not something a selector here can use. An
        unknown or deleted user must not break a transcript, so an unresolvable
        ID renders as itself.
        """
        record = self.identify(user_id)
        if record and record.get("external"):
            return f"@{record['display'] or record['handle'] or user_id} (ext)"
        return ("@" + record["handle"]) if record and record["handle"] else f"@{user_id}"

    # -- users found outside users.list ------------------------------------

    def _outside_users(self) -> Dict[str, Dict[str, Any]]:
        if self._outside is None:
            cached = self.cache.get("external-users", EXTERNAL_USERS_TTL)
            self._outside = dict(cached) if isinstance(cached, dict) else {}
        return self._outside

    def _outsider(
        self,
        user_id: str,
        handle: str,
        profile: Dict[str, Any],
        team: Optional[str],
        is_bot: bool = False,
        deleted: bool = False,
    ) -> Dict[str, Any]:
        """The index record for someone found outside users.list.

        External when their team is not ours. For them the real name leads:
        their display name was chosen for their own workspace, and the full
        name is what a reader on this side recognizes. No email is kept; the
        cache has no business holding another company's addresses.
        """
        real_name = profile.get("real_name") or ""
        external = bool(team) and team != self.cache.team_id
        if external:
            display = real_name or profile.get("display_name") or handle
        else:
            display = profile.get("display_name") or real_name or handle
        return {
            "id": user_id,
            "handle": handle,
            "real_name": real_name,
            "display": display,
            "email": None,
            "is_bot": is_bot,
            "deleted": deleted,
            "team": team or None,
            "external": external,
        }

    def _remember(self, record: Dict[str, Any]) -> None:
        """Hold a record for this process; persist it if it is external.

        A colleague found by users.info is held in memory only: the next
        users.list rebuild picks them up, and a second copy would only go stale.
        """
        outside = self._outside_users()
        if outside.get(record["id"]) == record:
            return
        outside[record["id"]] = record
        if record["external"]:
            self.cache.put(
                "external-users",
                {key: value for key, value in outside.items() if value["external"]},
            )

    def channel_name_for(self, channel_id: str) -> Optional[str]:
        """A '#name' for a channel ID, or None when it is not one we know.

        Part of the NameSource protocol the renderer depends on, so a
        '<#C123>' mention with no inline label still renders as a name.
        """
        conversation = self.conversation(channel_id)
        if conversation and conversation.get("name"):
            return "#" + conversation["name"]
        return None

    def find_users(self, selector: str) -> List[Dict[str, Any]]:
        """Match a selector against handle, real name, and display name.

        An exact handle match short-circuits. Without that, '@joan' would be
        ambiguous purely because '@joan-w' also exists, which would make the
        common case fail for a reason the caller cannot act on.
        """
        needle = (selector or "").lstrip("@").strip().lower()
        if not needle:
            return []
        users = list(self.users().values())

        # Slack handles are unique per workspace, so an exact handle match is
        # unambiguous by construction and wins outright. That is what lets
        # '@joan' resolve even though '@joan-w' also exists.
        exact_handle = [u for u in users if u["handle"].lower() == needle]
        if exact_handle:
            return exact_handle

        # Display and real names are NOT unique, so they must never
        # short-circuit. Falling through to gather every candidate is what turns
        # a shared first name into a visible ambiguity the caller can act on,
        # rather than a silent pick of whoever set it as their display name.
        return [
            u for u in users
            if needle in u["handle"].lower()
            or needle in u["real_name"].lower()
            or needle in u["display"].lower()
        ]

    # -- conversations ------------------------------------------------------

    def conversations(self) -> List[Dict[str, Any]]:
        if self._conversations is not None:
            return self._conversations

        cached = self.cache.get("conversations", CONVERSATIONS_TTL)
        if cached is not None:
            self._conversations = cached
            return cached

        rows: List[Dict[str, Any]] = []
        seen = set()
        # Archived rows are fetched too. Their history is readable, and the
        # channels command's --include-archived exists to show them; filtering
        # happens locally so the cache holds the whole inventory (D5, D7).
        for page in self.client.paginate(
            "users.conversations",
            "channels",
            types="public_channel,private_channel,im,mpim",
            limit=200,
            exclude_archived=False,
        ):
            for conversation in page.items:
                if conversation["id"] in seen:
                    continue
                seen.add(conversation["id"])
                rows.append(self._shape(conversation))

        self._conversations = rows
        self.cache.put("conversations", rows)
        return rows

    def _shape(self, conversation: Dict[str, Any]) -> Dict[str, Any]:
        kind = classify(conversation)
        name = conversation.get("name") or ""
        members: List[str] = []
        label = name

        if kind == "im":
            other = conversation.get("user")
            members = [other] if other else []
            label = self.handle_for(other) if other else "(unknown)"
        elif kind == "mpim":
            # Handles from the name, resolved to IDs where possible. This is a
            # cheap pre-filter only: the name records the handles people had
            # when the conversation was created. The resolver verifies
            # membership with conversations.members and replaces this list
            # with the verified set before anything is read (D6).
            handles = [h for h in parse_mpim_name(name) if h]
            by_handle = {u["handle"]: u["id"] for u in self.users().values()}
            members = [by_handle[h] for h in handles if h in by_handle]
            label = ", ".join("@" + h for h in handles if h)
        else:
            label = "#" + name if name else conversation["id"]

        return {
            "id": conversation["id"],
            "kind": kind,
            "name": name,
            "label": label,
            "members": members,
            "member_handles": parse_mpim_name(name) if kind == "mpim" else [],
            "num_members": conversation.get("num_members"),
            "archived": bool(conversation.get("is_archived")),
        }

    def conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        for row in self.conversations():
            if row["id"] == conversation_id:
                return row
        return None

    def find_conversations_by_name(self, selector: str) -> List[Dict[str, Any]]:
        """Every conversation matching a name. Deliberately returns a list.

        Group DM names are not unique: two distinct IDs in the surveyed workspace
        share 'mpdm-sam--lee--bea-1'. Returning one match would
        silently pick whichever came back first (D6).
        """
        needle = (selector or "").lstrip("#").strip().lower()
        if not needle:
            return []
        rows = self.conversations()
        exact = [c for c in rows if c["name"].lower() == needle]
        if not exact:
            exact = [c for c in rows if c["id"].lower() == needle]
        # An archived conversation never wins a name over a live one. Slack lets
        # a name be reused once the old channel is archived, so without this a
        # perfectly ordinary channel name would be ambiguous with its own past.
        # If only archived rows match, they are returned: their history is
        # readable and the caller asked for that name.
        live = [c for c in exact if not c["archived"]]
        return live or exact

    def find_archived_namesakes(self, selector: str) -> List[Dict[str, Any]]:
        """The archived conversations a name selector also matched.

        Lives beside ``find_conversations_by_name`` so the warning about what
        was set aside uses the same normalization as the resolution that set
        it aside. An ID selector has no namesakes.
        """
        needle = (selector or "").lstrip("#").strip().lower()
        if not needle:
            return []
        return [c for c in self.conversations()
                if c["archived"] and c["name"].lower() == needle]
