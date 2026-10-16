"""``slackline-read thread``: one exchange, root and replies, as a unit (D13, P4).

The overlap with ``conversation --threads full`` is real and worth naming.
``conversation`` answers "what happened here over this window", and expands
threads inside that answer. ``thread`` answers "what was said in this specific
exchange", for a caller that already holds the ts: from a Slack permalink, or
from the ``ts`` field of an earlier read. Asking the first question to get the
second means guessing a window wide enough to contain the thread and then
discarding everything else.

Truncation keeps the beginning of the thread: Slack returns the parent first and
replies in order, and the ceiling stops the fetch at a page boundary rather than
cutting a fetched page after the fact, so the cursor in the trailing ``page``
record resumes at the first reply not yet delivered (D16). A thread read without
its root is not a thread, and a continuation page is explicitly a fragment of
one, which is why every message on it is marked as a reply.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..errors import AmbiguousSelector, ArgumentError, NotFound
from ..timeparse import to_iso
from ..transcript import MAX_PAGES, collect, keep, message_record, page_record
from ._common import TIME_FLAGS, new_selection, parse_selector_arg, validate_selection

DEFAULT_LIMIT = 200
DEFAULT_MAX_MESSAGES = 500

HELP = """\
Usage: slackline-read thread [selector] --ts TIMESTAMP [options]

Read one thread: the root message and every reply beneath it.

Selectors (use exactly one kind, and it must name exactly one conversation):
  --channel NAME_OR_ID     The channel the thread lives in
  --with PERSON            Repeatable. Group DMs match on member set, not name.
                           PERSON is @handle for exactly that person; a bare
                           name searches names and fails if two people match
  --conversation ID

Required:
  --ts TIMESTAMP           A message's ts, e.g. 1756310012.000100: the 'ts'
                           field of any message in the thread, or the trailing
                           number in a Slack permalink. A reply's ts is accepted
                           and costs one extra call to find the root.

Options:
  --limit N                At most this many replies per API page (default
                           200); a request near the ceiling asks for fewer
  --max-messages N         Ceiling on messages returned, root included
                           (default 500). A ceiling of 1 yields the root
                           plus one reply, since a page of zero cannot be
                           requested
  --all-pages              Follow cursors until the thread is exhausted
  --cursor CURSOR          Continue a previous incomplete read of this thread.
                           Pass the thread_ts from that read's records as --ts,
                           not a reply's ts
  --include-system         Include join and leave messages (excluded by default)
  --exclude-bots           Exclude bot messages (included by default)
  --no-reactions           Omit reaction summaries
  --timezone IANA_NAME     Default: the system timezone
  --format text|jsonl|json|raw

A time window is not accepted here: a thread is identified by its ts, and
replies to an old root can arrive at any time.

Exit codes:
  0  read, including a truncated read
  4  the selector named more than one conversation
  5  no such thread, or not visible to you
"""


def parse(argv: List[str]) -> Dict[str, Any]:
    """Every argument check, and nothing that needs a client (D18)."""
    options: Dict[str, Any] = {
        "selection": new_selection(),
        "ts": None,
        "limit": DEFAULT_LIMIT,
        "max_messages": DEFAULT_MAX_MESSAGES,
        "all_pages": False,
        "cursor": None,
        "include_system": False,
        "exclude_bots": False,
        "reactions": True,
    }
    index = 0
    while index < len(argv):
        consumed = parse_selector_arg(argv, index, options["selection"])
        if consumed >= 0:
            index = consumed + 1
            continue

        arg = argv[index]
        if arg in ("--ts", "--limit", "--max-messages", "--cursor"):
            index += 1
            if index >= len(argv):
                raise ArgumentError(f"{arg} requires a value")
            value = argv[index]
            if arg == "--ts":
                options["ts"] = value
            elif arg == "--cursor":
                options["cursor"] = value
            else:
                key = "limit" if arg == "--limit" else "max_messages"
                try:
                    options[key] = int(value)
                except ValueError:
                    raise ArgumentError(f"{arg} expects a whole number, got {value!r}") from None
                if options[key] <= 0:
                    raise ArgumentError(f"{arg} must be greater than zero")
        elif arg == "--all-pages":
            options["all_pages"] = True
        elif arg == "--include-system":
            options["include_system"] = True
        elif arg == "--exclude-bots":
            options["exclude_bots"] = True
        elif arg == "--no-reactions":
            options["reactions"] = False
        else:
            raise ArgumentError(f"unknown option for 'thread': {arg}")
        index += 1

    # Refused rather than ignored. A caller who passes --since would otherwise
    # believe a window was applied, and act on a transcript that ignored it.
    windowing = [flag for flag in TIME_FLAGS
                 if options["selection"][flag.lstrip("-").replace("-", "_")]]
    if windowing:
        raise ArgumentError(
            f"'thread' does not take a time window ({', '.join(windowing)}). "
            f"A thread is identified by its --ts. Use 'conversation --threads full' "
            f"to read threads across a window."
        )

    if not options["ts"]:
        raise ArgumentError(
            "thread requires --ts, a message's timestamp. It is the 'ts' field of "
            "any message in the thread, and the trailing number in a Slack "
            "permalink (archives/C123/p1756310012000100 is 1756310012.000100)."
        )
    validate_selection(options["selection"])
    return options


def _root_of(messages: List[Dict[str, Any]], fallback: str) -> str:
    """The thread's root ts, as the replies themselves report it."""
    for message in messages:
        if message.get("thread_ts") and message["thread_ts"] != message.get("ts"):
            return message["thread_ts"]
    return fallback


def run(ctx, argv: List[str], options: Optional[Dict[str, Any]] = None) -> int:
    if options is None:
        options = parse(argv)
    selection = options["selection"]
    timezone = selection["timezone"]

    matches = ctx.resolver().resolve(
        channels=selection["channels"] or None,
        with_users=selection["with_users"] or None,
        conversations=selection["conversations"] or None,
    )
    if len(matches) > 1:
        # A ts belongs to exactly one conversation, so there is no reading of
        # this that returns the right thing. Never guessed (D8).
        raise AmbiguousSelector(
            f"selector matched {len(matches)} conversations; a thread lives in "
            f"exactly one. Re-run with a single --conversation ID.",
            candidates=[f"{m['id']}  {m.get('label') or m.get('name') or ''}".rstrip()
                        for m in matches],
        )
    conversation = matches[0]

    fetched = collect(
        ctx.client,
        "conversations.replies",
        "messages",
        limit=options["limit"],
        max_messages=options["max_messages"],
        all_pages=options["all_pages"],
        cursor=options["cursor"],
        keep=lambda m: keep(m, options["include_system"], options["exclude_bots"]),
        first_page_reserve=1,  # Slack counts the parent outside `limit`
        channel=conversation["id"],
        ts=options["ts"],
    )

    if fetched.seen == 0:
        # Slack can answer ok:true with no messages for a well-formed ts that
        # names nothing. Emitting an empty transcript and exiting 0 would assert
        # that the thread exists and is empty, which is a different, wrong fact.
        # The same holds for a continuation page: an empty one is not a thread
        # that ran out, it is a cursor that named nothing.
        raise NotFound(
            f"no thread at ts {options['ts']} in {conversation['id']}, or it is "
            f"not visible to you."
        )

    # The root as far as it is known. Re-derived from the kept messages below
    # only when no redirect established it; after a redirect the kept set may
    # have been emptied by filtering, and the root is known regardless.
    known_root: Optional[str] = None
    if not options["cursor"]:
        # Slack answers a reply's ts with that one message. The help promises
        # any message's ts works, so when the ts named a reply, read the thread
        # again from the root it points at. One extra read-only call, announced.
        # If the single message Slack returned was itself filtered out, no
        # redirect fires; the transcript is then honestly empty.
        actual_root = _root_of(fetched.messages, options["ts"])
        if actual_root == options["ts"] and fetched.seen and not fetched.messages:
            # Everything Slack returned was filtered out, so the kept set
            # cannot say whether the ts named a reply. One unfiltered probe of
            # a single message settles it; otherwise a filtered reply would
            # yield an empty transcript headed by the reply's ts as its root.
            probe = collect(
                ctx.client,
                "conversations.replies",
                "messages",
                limit=1,
                max_messages=1,
                all_pages=False,
                cursor=None,
                keep=lambda m: True,
                channel=conversation["id"],
                ts=options["ts"],
            )
            actual_root = _root_of(probe.messages, options["ts"])
        if actual_root != options["ts"]:
            known_root = actual_root
            ctx.warn(
                f"--ts {options['ts']} names a reply; reading its thread from the "
                f"root {actual_root}"
            )
            fetched = collect(
                ctx.client,
                "conversations.replies",
                "messages",
                limit=options["limit"],
                max_messages=options["max_messages"],
                all_pages=options["all_pages"],
                cursor=None,
                keep=lambda m: keep(m, options["include_system"], options["exclude_bots"]),
                first_page_reserve=1,
                channel=conversation["id"],
                ts=actual_root,
            )
            if fetched.seen == 0:
                raise NotFound(
                    f"the reply at ts {options['ts']} points at root {actual_root}, "
                    f"which returned nothing in {conversation['id']}."
                )

    collected: List[Dict[str, Any]] = list(fetched.messages)
    filtered = fetched.filtered
    next_cursor = fetched.next_cursor
    truncated_by: Optional[str] = fetched.truncated_by

    # Slack sends the parent first and replies in order, but sort anyway: the
    # transcript's order is the order the exchange happened, not the payload's.
    collected.sort(key=lambda m: float(m.get("ts") or 0))

    root_ts = known_root or _root_of(collected, options["ts"])

    if options["cursor"]:
        # The parent rides only on the first page (observed live, and the fake
        # models it). Kept as a cheap guard anyway: a continuation page must
        # never re-emit the root as a non-reply; the first page showed it.
        collected = [m for m in collected if m.get("ts") != root_ts]

    ctx.emit(
        {
            "type": "conversation",
            "id": conversation["id"],
            "kind": conversation["kind"],
            "label": conversation.get("label"),
            "name": conversation.get("name") or None,
            "thread_ts": root_ts,
        }
    )

    for message in collected:
        ctx.emit(
            message_record(
                message,
                conversation["id"],
                ctx.directory(),
                timezone,
                options["reactions"],
                is_reply=message.get("ts") != root_ts,
            )
        )

    if truncated_by == "max-messages":
        ctx.warn(
            f"{conversation['id']}: thread stopped at --max-messages "
            f"{options['max_messages']}. The read is incomplete; raise the ceiling, "
            f"or continue with --ts {root_ts} --cursor from the page record."
        )
    elif truncated_by == "max-pages":
        ctx.warn(
            f"{conversation['id']}: thread stopped after {MAX_PAGES} pages with more "
            f"available; continue with --ts {root_ts} --cursor from the page record."
        )
    ctx.emit(page_record(
        conversation["id"],
        returned=len(collected),
        filtered=filtered,
        next_cursor=next_cursor,
        truncated_by=truncated_by,
        thread_ts=root_ts,
    ))

    # A truncated read succeeded. Exit codes are for failures (D16).
    return 0
