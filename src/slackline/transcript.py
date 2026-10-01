"""The shape of one rendered message, shared by every command that emits one.

``conversation`` and ``thread`` both return transcripts, and they must return
the same record for the same message. If the two drift, the JSONL contract
quietly becomes two contracts and the text formatter's ``message`` renderer
stops being reusable. Same reasoning that put selector parsing in ``_common``:
an agent that learns the output shape from one command should be right about
the other.

The same module holds :func:`collect`, the bounded fetch loop both commands run.
It asks Slack for exactly the remaining message budget on every request, so the
ceiling always coincides with a page boundary and the cursor Slack hands back
resumes at the first undelivered message. A ceiling applied after the fact, by
cutting a page that was fetched in full, leaves the cut page's remainder behind a
cursor that points past it: continuation then silently skips those messages,
which is the failure D16 exists to prevent (D15, D16).
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, NamedTuple, Optional

from .render import render_message
from .timeparse import to_iso

# Subtypes that are membership churn rather than conversation. 9% of sampled
# messages, and none of it is content a reader wants.
NOISE_SUBTYPES = {"channel_join", "channel_leave", "group_join", "group_leave"}

#: Pages one collect() call may fetch. At the default limit that is 20,000
#: top-level messages per call, far beyond any read this tool exists for. A
#: server that keeps returning a cursor with nothing behind it is the same
#: hostile or broken intermediary D17 capped Retry-After for, and this loop is
#: the one place whose whole job is to be bounded (D15).
MAX_PAGES = 100


def keep(message: Dict[str, Any], include_system: bool, exclude_bots: bool) -> bool:
    """Whether a message belongs in the transcript.

    Callers count what this rejects and report the count. Filtering that is not
    reported is indistinguishable from a conversation that was quiet.
    """
    subtype = message.get("subtype")
    if subtype in NOISE_SUBTYPES and not include_system:
        return False
    if exclude_bots and (message.get("bot_id") or subtype == "bot_message"):
        return False
    return True


def message_record(
    message: Dict[str, Any],
    conversation_id: str,
    directory,
    timezone: Optional[str] = None,
    reactions: bool = True,
    is_reply: bool = False,
) -> Dict[str, Any]:
    user_id = message.get("user") or ""
    # Before rendering, so the sender's own profile also names them wherever
    # this message or a later one mentions them (D7).
    directory.learn_from_message(message)
    user = directory.identify(user_id) if user_id else None
    return {
        "type": "message",
        "conversation_id": conversation_id,
        # Both representations ship. Slack's ts is the message ID and the thread
        # key, so dropping it makes `thread` and cursors impossible (D13).
        "ts": message.get("ts"),
        "time": to_iso(message.get("ts"), timezone),
        "user": {
            "id": user_id or None,
            "name": user["handle"] if user else (message.get("username") or None),
            "display": user["display"] if user else (message.get("username") or None),
            "is_bot": bool(message.get("bot_id")) or (user["is_bot"] if user else False),
            # From another workspace, in a channel shared with it. ``name`` is
            # then their handle over there, which no selector here accepts.
            "external": bool(user and user.get("external")),
            "team": (user.get("team") if user else None) or message.get("user_team"),
        },
        "text": render_message(message, directory, include_reactions=False),
        "thread_ts": message.get("thread_ts"),
        "reply_count": message.get("reply_count", 0),
        "is_reply": is_reply,
        "subtype": message.get("subtype"),
        "edited": bool(message.get("edited")),
        "reactions": (
            [{"name": r.get("name"), "count": r.get("count", 0)}
             for r in message.get("reactions") or []]
            if reactions else []
        ),
        "_raw": message,
    }


def page_record(
    conversation_id: str,
    *,
    returned: int,
    filtered: int,
    next_cursor: str,
    truncated_by: Optional[str],
    incomplete_threads: int = 0,
    thread_ts: Optional[str] = None,
) -> Dict[str, Any]:
    """The trailing record of every transcript, built in one place (D16).

    ``conversation`` and ``thread`` emit the same shape; ``thread`` adds
    ``thread_ts``. ``has_more`` is true exactly when the cursor is usable: a
    cursor is the only form of "more" a caller can act on.
    """
    record: Dict[str, Any] = {
        "type": "page",
        "conversation_id": conversation_id,
        "returned": returned,
        "filtered": filtered,
        "incomplete_threads": incomplete_threads,
        "has_more": bool(next_cursor),
        "next_cursor": next_cursor,
        "truncated_by": truncated_by,
    }
    if thread_ts is not None:
        record["thread_ts"] = thread_ts
    return record


class Collected(NamedTuple):
    """What one bounded fetch produced, and how to continue it."""

    messages: List[Dict[str, Any]]  # kept, in the order Slack returned them
    filtered: int                   # rejected by ``keep``
    seen: int                       # kept + filtered
    next_cursor: str                # "" when Slack has nothing further
    truncated_by: Optional[str]     # "max-messages", or None
    pages: int


def collect(
    client,
    method: str,
    collection: str,
    *,
    limit: int,
    max_messages: Optional[int],
    all_pages: bool,
    cursor: Optional[str],
    keep: Callable[[Dict[str, Any]], bool],
    first_page_reserve: int = 0,
    **params: Any,
) -> Collected:
    """Fetch pages until the budget is spent, the pages run out, or one page is done.

    ``max_messages`` is the budget of kept messages; ``None`` means no budget and
    every request asks for ``limit``. ``first_page_reserve`` lowers the first
    request (the one with no cursor) by that many: ``conversations.replies``
    returns the parent in addition to ``limit`` replies on its first page, so
    ``thread`` reserves one to keep its ceiling literal. Filtered messages
    occupy slots in a page but do not spend budget, so a page that came back
    light is followed by another request for what is still owed. The one thing this loop never does is cut a
    page after fetching it: Slack's ``limit`` is the cut, and Slack's cursor is
    therefore always exact (D16). The ceiling is honored by request sizing rather
    than by cutting, so a server that delivers more than it was asked for can
    overshoot the ceiling by at most one page; that is preferred to a cut, because
    a cut is exactly what makes the cursor inexact.

    ``--all-pages`` asks for every page, but not for an unending one: with or
    without a budget the call stops after :data:`MAX_PAGES` pages and reports
    ``truncated_by="max-pages"`` with the cursor, so a server that keeps handing
    out cursors with empty or fully filtered pages cannot hold the process (D15).
    """
    messages: List[Dict[str, Any]] = []
    filtered = 0
    pages = 0
    remaining = max_messages
    next_cursor = ""

    first_request = True
    while True:
        # Clamped at 1: the budget can go negative if a page delivers more than
        # it was asked for, and a non-positive limit must never reach the wire.
        page_limit = limit if remaining is None else max(1, min(limit, remaining))
        if first_request and not cursor and first_page_reserve:
            # The reserve cannot take the request below one: a non-positive
            # limit must never reach the wire. So a budget of 1 still asks for
            # one reply, and thread --max-messages 1 yields the root plus one.
            page_limit = max(1, page_limit - first_page_reserve)
        first_request = False
        body = client.call(method, cursor=cursor or None, limit=page_limit, **params)
        pages += 1
        for item in body.get(collection) or []:
            if keep(item):
                messages.append(item)
                if remaining is not None:
                    remaining -= 1
            else:
                filtered += 1
        next_cursor = (body.get("response_metadata") or {}).get("next_cursor") or ""

        if not next_cursor:
            return Collected(messages, filtered, len(messages) + filtered, "", None, pages)
        if remaining is not None and remaining <= 0:
            # <= rather than ==: a page that over-delivers must still stop the
            # loop. This is the one place whose whole job is to be bounded, and
            # it does not get to depend on the server's arithmetic for that.
            # Checked before the page ceiling: the caller's budget is the more
            # specific cause, and "max-pages" means nothing else stopped it.
            return Collected(messages, filtered, len(messages) + filtered, next_cursor,
                             "max-messages", pages)
        if pages >= MAX_PAGES:
            return Collected(messages, filtered, len(messages) + filtered, next_cursor,
                             "max-pages", pages)
        if not all_pages:
            return Collected(messages, filtered, len(messages) + filtered, next_cursor,
                             None, pages)
        cursor = next_cursor
