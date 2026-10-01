"""``slackline-read conversation``: the command the whole project exists for.

Resolve a conversation, read a bounded time window of it, optionally expand
threads, and return something a language model can read.

Two behaviors here are load-bearing and easy to get wrong:

**A truncated read exits 0** (D16). It succeeded. Reporting failure would mislead
``set -e``, CI, and most agent harnesses into treating a working call as an error.
Continuation lives in the trailing ``page`` record instead, and for top-level
history it is exact: each request asks Slack for the remaining budget, so the
ceiling coincides with a page boundary and Slack's cursor resumes at the first
undelivered message. A ceiling applied by cutting a fetched page would leave that
page's remainder behind a cursor pointing past it, and ``--cursor`` would skip it
(see ``transcript.collect``). The one qualification is under ``--threads full``:
a broadcast reply whose root sits on the other side of a page boundary is emitted
from history on one page and again under its root on the next, because the
deduplication set is per invocation and a silent drop would be the worse failure.

**Filtering is always reported.** Whenever anything is dropped, the count appears
in that same record. A reader must always be able to tell that the transcript is
a subset, and by how much.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..errors import ArgumentError
from ..timeparse import to_iso, window
from ..transcript import MAX_PAGES, collect, keep, message_record, page_record
from ._common import new_selection, parse_selector_arg, validate_selection

THREAD_MODES = ("none", "full")
ORDERS = ("oldest", "newest")

DEFAULT_LIMIT = 200
DEFAULT_MAX_MESSAGES = 500

HELP = """\
Usage: slackline-read conversation [selector] [window] [options]

Read messages from a conversation.

Selectors (use exactly one kind):
  --channel NAME_OR_ID     Repeatable
  --with PERSON            Repeatable. Group DMs match on member set, not name
  --conversation ID        Repeatable

Selectors are repeatable. Several conversations are read in one invocation,
each with its own header and trailing page record.

Window (use at most one form):
  --since DURATION|DATE    e.g. 48h, 7d, 2026-08-25
  --between START END
  --after START [--before END]
  --timezone IANA_NAME     Default: the system timezone

Options:
  --threads none|full      Default: none. 'full' inlines replies beneath roots
  --limit N                At most this many messages per API page (default
                           200); a request near the ceiling asks for fewer.
                           Also the reply page size inside an expanded thread
  --max-messages N         Ceiling on top-level messages across all pages
                           (default 500). Inlined replies do not count toward
                           it: without --all-pages each thread contributes at
                           most one page of --limit replies
  --all-pages              Follow cursors until the window is exhausted. Also
                           removes the per-thread bound: every expanded thread
                           is followed to its last reply
  --cursor CURSOR          Continue a previous incomplete read. Applies to one
                           conversation, so it cannot be combined with several
  --order oldest|newest    Default: oldest, the order conversations are read in
  --include-system         Include join and leave messages (excluded by default)
  --exclude-bots           Exclude bot messages (included by default)
  --no-reactions           Omit reaction summaries
  --format text|jsonl|json|raw

A read that hits its ceiling exits 0 and reports the truncation in the trailing
'page' record. It succeeded; it just did not finish.
"""


def parse(argv: List[str]) -> Dict[str, Any]:
    """Every argument check, and nothing that needs a client.

    Runs before any credential is resolved, so an invalid invocation is exit 2
    whether or not a token exists (D18). The time window is computed here for
    the same reason: an inverted range or a bare number is an argument error.
    """
    options: Dict[str, Any] = {
        "selection": new_selection(),
        "threads": "none",
        "limit": DEFAULT_LIMIT,
        "max_messages": DEFAULT_MAX_MESSAGES,
        "all_pages": False,
        "cursor": None,
        "order": "oldest",
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
        if arg in ("--threads", "--limit", "--max-messages", "--cursor", "--order"):
            index += 1
            if index >= len(argv):
                raise ArgumentError(f"{arg} requires a value")
            value = argv[index]
            if arg == "--threads":
                if value not in THREAD_MODES:
                    raise ArgumentError(
                        f"unknown --threads mode {value!r}. Expected one of: "
                        + ", ".join(THREAD_MODES)
                    )
                options["threads"] = value
            elif arg == "--order":
                if value not in ORDERS:
                    raise ArgumentError(
                        f"unknown --order {value!r}. Expected one of: " + ", ".join(ORDERS)
                    )
                options["order"] = value
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
            raise ArgumentError(f"unknown option for 'conversation': {arg}")
        index += 1

    validate_selection(options["selection"])
    selection = options["selection"]
    options["window"] = window(
        since=selection["since"],
        between=selection["between"],
        after=selection["after"],
        before=selection["before"],
        timezone=selection["timezone"],
    )
    return options


def _kept(message: Dict[str, Any], options: Dict[str, Any]) -> bool:
    return keep(message, options["include_system"], options["exclude_bots"])


def run(ctx, argv: List[str], options: Optional[Dict[str, Any]] = None) -> int:
    if options is None:
        options = parse(argv)
    selection = options["selection"]
    oldest, latest = options["window"]
    timezone = selection["timezone"]

    conversations = ctx.resolver().resolve(
        channels=selection["channels"] or None,
        with_users=selection["with_users"] or None,
        conversations=selection["conversations"] or None,
    )

    if options["cursor"] and len(conversations) > 1:
        # A Slack cursor is an offset into one conversation's history. Applied
        # to a second conversation it would resume from an unrelated point and
        # report that read as complete. Never guessed (D8).
        raise ArgumentError(
            f"--cursor continues one conversation, but the selectors matched "
            f"{len(conversations)}. Re-run with a single --channel or "
            f"--conversation and the cursor that came from it."
        )

    for conversation in conversations:
        ctx.emit(
            {
                "type": "conversation",
                "id": conversation["id"],
                "kind": conversation["kind"],
                "label": conversation.get("label"),
                "name": conversation.get("name") or None,
                "range": {
                    "from": to_iso(oldest, timezone) if oldest else None,
                    "to": to_iso(latest, timezone) if latest else None,
                },
            }
        )

        fetched = collect(
            ctx.client,
            "conversations.history",
            "messages",
            limit=options["limit"],
            max_messages=options["max_messages"],
            all_pages=options["all_pages"],
            cursor=options["cursor"],
            keep=lambda m: _kept(m, options),
            channel=conversation["id"],
            oldest=f"{oldest:.6f}" if oldest else None,
            latest=f"{latest:.6f}" if latest else None,
        )
        collected: List[Dict[str, Any]] = list(fetched.messages)
        filtered = fetched.filtered
        next_cursor = fetched.next_cursor
        truncated_by: Optional[str] = fetched.truncated_by

        # Slack returns history in either order depending on the window (newest
        # first with no bounds, oldest first with `oldest` set, observed live).
        # Sort ascending before thread expansion so replies land under the
        # right root.
        collected.sort(key=lambda m: float(m.get("ts") or 0))

        # One group per top-level message: the root followed by its inlined
        # replies. --order newest reverses the groups, never the records, so a
        # reply always sits beneath its root (D13).
        groups: List[List[Dict[str, Any]]] = []
        incomplete_threads = 0
        # A thread_broadcast reply appears in channel history and again in its
        # thread's replies. Every ts is emitted once: the root is older than
        # its broadcast, so the root's expansion runs first and the history
        # copy is the one skipped. A broadcast whose root fell outside the
        # window is still emitted from history, because nothing else will.
        # A message with no ts cannot be a duplicate of anything, so it is
        # never checked against the set: a None key would make every later
        # ts-less message vanish uncounted.
        emitted: set = set()
        for message in collected:
            ts = message.get("ts")
            if ts and ts in emitted:
                continue
            if ts:
                emitted.add(ts)
            group: List[Dict[str, Any]] = [
                message_record(message, conversation["id"], ctx.directory(),
                               timezone, options["reactions"])
            ]
            groups.append(group)
            if (
                options["threads"] == "full"
                and message.get("reply_count")
                and message.get("thread_ts") in (None, message.get("ts"))
            ):
                # Replies are paginated like everything else. A thread longer
                # than one page is not silently shortened: without --all-pages
                # it stops after one page and is counted as incomplete, so the
                # reader knows the transcript is a subset (invariants 4 and 5).
                replies = collect(
                    ctx.client,
                    "conversations.replies",
                    "messages",
                    limit=options["limit"],
                    max_messages=None,
                    all_pages=options["all_pages"],
                    cursor=None,
                    keep=lambda m: _kept(m, options),
                    channel=conversation["id"],
                    ts=message["ts"],
                )
                filtered += replies.filtered
                if replies.next_cursor:
                    incomplete_threads += 1
                for reply in replies.messages:
                    reply_ts = reply.get("ts")
                    if reply_ts and reply_ts in emitted:
                        continue  # the root, or a copy already rendered
                    if reply_ts:
                        emitted.add(reply_ts)
                    group.append(
                        message_record(reply, conversation["id"], ctx.directory(),
                                       timezone, options["reactions"], is_reply=True)
                    )

        if options["order"] == "newest":
            groups.reverse()
        records: List[Dict[str, Any]] = [record for group in groups for record in group]

        for record in records:
            ctx.emit(record)

        if truncated_by == "max-messages":
            ctx.warn(
                f"{conversation['id']}: stopped at --max-messages "
                f"{options['max_messages']}. The read is incomplete; raise the "
                f"ceiling or pass --all-pages."
            )
        elif truncated_by == "max-pages":
            ctx.warn(
                f"{conversation['id']}: stopped after {MAX_PAGES} pages with more "
                f"available; continue with --cursor from the page record."
            )
        if incomplete_threads:
            ctx.warn(
                f"{conversation['id']}: {incomplete_threads} thread"
                f"{'' if incomplete_threads == 1 else 's'} not fully expanded; "
                f"pass --all-pages to follow reply cursors."
            )
        ctx.emit(page_record(
            conversation["id"],
            returned=len([r for r in records if r["type"] == "message"]),
            filtered=filtered,
            next_cursor=next_cursor,
            truncated_by=truncated_by,
            incomplete_threads=incomplete_threads,
        ))

    # A truncated read succeeded. Exit codes are for failures (D16).
    return 0
