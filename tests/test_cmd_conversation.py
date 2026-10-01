"""The main event: windowed, thread-aware, bounded reads (D11, D15, D16)."""
from __future__ import annotations

import json
import pathlib

import pytest

from slackline import cli_read
from slackline.cache import Cache
from slackline.commands import conversation as conversation_cmd
from slackline.directory import Directory
from slackline.resolve import Resolver

DIR_FIXTURE = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "directory.json").read_text()
)
MSG = json.loads((pathlib.Path(__file__).parent / "fixtures" / "messages.json").read_text())


def history(*keys, cursor=""):
    return {
        "ok": True,
        "messages": [MSG[k] for k in keys],
        "has_more": bool(cursor),
        "response_metadata": {"next_cursor": cursor},
    }


@pytest.fixture
def ctx_for(tmp_path, fake_client):
    def build(history_pages, fmt="jsonl", replies=None):
        responses = {
            "users.list": {"ok": True, "members": DIR_FIXTURE["users"]},
            "users.conversations": {"ok": True, "channels": DIR_FIXTURE["conversations"]},
            "conversations.history": history_pages,
            "conversations.replies": replies or {"ok": True, "messages": []},
            "conversations.members": {"ok": True, "members": ["U001", "U002"],
                                      "response_metadata": {"next_cursor": ""}},
        }
        client = fake_client(responses)
        ctx = cli_read.Context(client=client, fmt=fmt)
        cache = Cache(root=tmp_path, team_id="T1")
        ctx._directory = Directory(client, cache, "U001")
        ctx._resolver = Resolver(client, ctx._directory, "U001", cache=cache)
        ctx._identity = {"user_id": "U001", "team_id": "T1"}
        return ctx

    return build


def records(ctx, argv, capsys):
    code = conversation_cmd.run(ctx, list(argv))
    ctx.flush()
    out = capsys.readouterr().out
    return code, [json.loads(line) for line in out.strip().splitlines() if line]


def messages_of(rows):
    return [r for r in rows if r["type"] == "message"]


def page_of(rows):
    return next(r for r in rows if r["type"] == "page")


# --- ordering and content --------------------------------------------------


def test_messages_come_back_oldest_first(ctx_for, capsys):
    """Conversations are understood in the order they happened (D14)."""
    ctx = ctx_for(history("thread_reply", "plain_text"))
    _, rows = records(ctx, ["--channel", "#announcements"], capsys)
    timestamps = [m["ts"] for m in messages_of(rows)]
    assert timestamps == sorted(timestamps)


def test_newest_first_is_available_explicitly(ctx_for, capsys):
    ctx = ctx_for(history("plain_text", "thread_reply"))
    _, rows = records(ctx, ["--channel", "#announcements", "--order", "newest"], capsys)
    timestamps = [m["ts"] for m in messages_of(rows)]
    assert timestamps == sorted(timestamps, reverse=True)


def test_each_message_carries_both_raw_ts_and_iso_time(ctx_for, capsys):
    """D13: ts is the message ID and thread key; time is for humans."""
    ctx = ctx_for(history("plain_text"))
    _, rows = records(ctx, ["--channel", "#announcements"], capsys)
    message = messages_of(rows)[0]
    assert message["ts"] == "1756310000.000100"
    assert message["time"].startswith("20")
    assert "T" in message["time"]


def test_message_text_is_rendered_not_raw(ctx_for, capsys):
    ctx = ctx_for(history("mrkdwn_markup"))
    _, rows = records(ctx, ["--channel", "#announcements"], capsys)
    text = messages_of(rows)[0]["text"]
    assert "<@U004>" not in text
    assert "@kim" in text


def test_a_text_less_message_still_produces_content(ctx_for, capsys):
    """The whole point of the renderer, asserted at the command level."""
    ctx = ctx_for(history("attachment_only"))
    _, rows = records(ctx, ["--channel", "#announcements"], capsys)
    assert messages_of(rows)[0]["text"].strip()


def test_the_conversation_header_identifies_what_was_read(ctx_for, capsys):
    ctx = ctx_for(history("plain_text"))
    _, rows = records(ctx, ["--channel", "#announcements"], capsys)
    header = rows[0]
    assert header["type"] == "conversation"
    assert header["id"] == "C100"


# --- filtering (D11) -------------------------------------------------------


def test_join_and_leave_noise_is_excluded_by_default(ctx_for, capsys):
    ctx = ctx_for(history("plain_text", "channel_join", "channel_leave"))
    _, rows = records(ctx, ["--channel", "#announcements"], capsys)
    subtypes = {m.get("subtype") for m in messages_of(rows)}
    assert "channel_join" not in subtypes and "channel_leave" not in subtypes


def test_system_messages_can_be_restored(ctx_for, capsys):
    ctx = ctx_for(history("plain_text", "channel_join"))
    _, rows = records(ctx, ["--channel", "#announcements", "--include-system"], capsys)
    assert "channel_join" in {m.get("subtype") for m in messages_of(rows)}


def test_bot_messages_are_included_by_default(ctx_for, capsys):
    """CI results and deploy notices are often exactly what the reader wants."""
    ctx = ctx_for(history("plain_text", "bot_with_text"))
    _, rows = records(ctx, ["--channel", "#announcements"], capsys)
    assert len(messages_of(rows)) == 2


def test_bot_messages_can_be_excluded(ctx_for, capsys):
    ctx = ctx_for(history("plain_text", "bot_with_text"))
    _, rows = records(ctx, ["--channel", "#announcements", "--exclude-bots"], capsys)
    assert len(messages_of(rows)) == 1


def test_the_number_filtered_is_always_reported(ctx_for, capsys):
    """The reader must know the transcript is a subset, and by how much."""
    ctx = ctx_for(history("plain_text", "channel_join", "channel_leave"))
    _, rows = records(ctx, ["--channel", "#announcements"], capsys)
    assert page_of(rows)["filtered"] == 2


def test_deleted_messages_are_kept_as_markers(ctx_for, capsys):
    """Their absence would itself be information."""
    ctx = ctx_for(history("plain_text", "tombstone"))
    _, rows = records(ctx, ["--channel", "#announcements"], capsys)
    assert any("deleted" in m["text"].lower() for m in messages_of(rows))


# --- bounds and continuation (D15, D16) ------------------------------------


def test_truncation_exits_zero_and_says_so(ctx_for, capsys):
    """The correction to the seed proposal, asserted.

    A read that succeeded must not report failure. Exit codes are for failures;
    the payload carries the nuance.
    """
    ctx = ctx_for(history("plain_text", "mrkdwn_markup", "bot_with_text"))
    code, rows = records(
        ctx, ["--channel", "#announcements", "--max-messages", "2"], capsys
    )
    assert code == 0, "a truncated read is a successful read"
    page = page_of(rows)
    assert page["truncated_by"] == "max-messages"
    assert page["has_more"] is True
    assert len(messages_of(rows)) == 2


def test_a_complete_read_reports_no_truncation(ctx_for, capsys):
    ctx = ctx_for(history("plain_text"))
    _, rows = records(ctx, ["--channel", "#announcements"], capsys)
    page = page_of(rows)
    assert page["has_more"] is False
    assert page["truncated_by"] is None
    assert page["next_cursor"] == ""


def test_a_cursor_is_preserved_for_continuation(ctx_for, capsys):
    ctx = ctx_for([history("plain_text", cursor="abc123")])
    _, rows = records(ctx, ["--channel", "#announcements"], capsys)
    assert page_of(rows)["next_cursor"] == "abc123"


def test_all_pages_follows_the_cursor(ctx_for, capsys):
    ctx = ctx_for([history("plain_text", cursor="c1"), history("bot_with_text")])
    _, rows = records(ctx, ["--channel", "#announcements", "--all-pages"], capsys)
    assert len(messages_of(rows)) == 2
    assert page_of(rows)["has_more"] is False


# --- threads ---------------------------------------------------------------


def test_threads_are_not_expanded_by_default(ctx_for, capsys):
    ctx = ctx_for(
        history("thread_parent"),
        replies={"ok": True, "messages": [MSG["thread_parent"], MSG["thread_reply"]]},
    )
    _, rows = records(ctx, ["--channel", "#announcements"], capsys)
    assert len(messages_of(rows)) == 1
    assert messages_of(rows)[0]["reply_count"] == 2


def test_threads_full_inlines_replies(ctx_for, capsys):
    ctx = ctx_for(
        history("thread_parent"),
        replies={"ok": True, "messages": [MSG["thread_parent"], MSG["thread_reply"]]},
    )
    _, rows = records(ctx, ["--channel", "#announcements", "--threads", "full"], capsys)
    messages = messages_of(rows)
    assert len(messages) == 2
    assert any(m.get("is_reply") for m in messages)


def test_an_unknown_threads_mode_is_an_argument_error(ctx_for):
    from slackline import errors

    ctx = ctx_for(history("plain_text"))
    with pytest.raises(errors.ArgumentError):
        conversation_cmd.run(ctx, ["--channel", "#announcements", "--threads", "sideways"])


# --- selectors -------------------------------------------------------------


def test_no_selector_is_an_argument_error(ctx_for):
    from slackline import errors

    ctx = ctx_for(history("plain_text"))
    with pytest.raises(errors.ArgumentError):
        conversation_cmd.run(ctx, ["--since", "48h"])


def test_a_time_window_reaches_slack_as_epoch_bounds(ctx_for, capsys):
    ctx = ctx_for(history("plain_text"))
    records(ctx, ["--channel", "#announcements", "--since", "48h"], capsys)
    call = next(c for c in ctx.client.calls if c[0] == "conversations.history")
    assert "oldest" in call[1] and call[1]["oldest"]


# --- text output -----------------------------------------------------------


def test_the_transcript_is_readable(ctx_for, capsys):
    ctx = ctx_for(history("plain_text"), fmt="text")
    conversation_cmd.run(ctx, ["--channel", "#announcements"])
    ctx.flush()
    out = capsys.readouterr().out
    assert "@joan" in out
    assert "postpone the migration" in out
    assert "[" in out, "timestamps should be bracketed"


def test_the_transcript_reports_truncation_visibly(ctx_for, capsys):
    ctx = ctx_for(history("plain_text", "mrkdwn_markup"), fmt="text")
    conversation_cmd.run(ctx, ["--channel", "#announcements", "--max-messages", "1"])
    ctx.flush()
    out = capsys.readouterr().out
    assert "truncat" in out.lower() or "more" in out.lower()


# --- continuation after a ceiling truncation (D16, gate fix 1) --------------


def synthetic_pages(per_page=4, count=3):
    """Slack-shaped history pages of invented messages, newest first per page."""
    pages = []
    index = 0
    for number in range(count):
        messages = [
            {"type": "message", "user": "U002", "text": f"m{index + k}",
             "ts": f"{1000 - (index + k)}.000000"}
            for k in range(per_page)
        ]
        index += per_page
        last = number == count - 1
        pages.append({
            "ok": True,
            "messages": messages,
            "has_more": not last,
            "response_metadata": {"next_cursor": "" if last else f"p{number + 1}"},
        })
    return pages


def all_ts(pages):
    return {m["ts"] for page in pages for m in page["messages"]}


def test_continuing_from_a_truncated_read_delivers_every_remaining_message(ctx_for, capsys):
    """The high finding from the Phase 1.0 gate.

    Before the fix the cursor after a cut pointed at the following page, so the
    rest of the cut page was never delivered to anyone.
    """
    pages = synthetic_pages()
    ctx = ctx_for(pages)
    _, first = records(ctx, ["--channel", "#announcements", "--max-messages", "2"], capsys)
    cursor = page_of(first)["next_cursor"]
    assert page_of(first)["truncated_by"] == "max-messages"
    assert cursor, "a truncated read must hand back a way to continue"

    _, second = records(
        ctx,
        ["--channel", "#announcements", "--cursor", cursor, "--all-pages",
         "--max-messages", "100"],
        capsys,
    )
    seen_first = {m["ts"] for m in messages_of(first)}
    seen_second = {m["ts"] for m in messages_of(second)}
    assert seen_first | seen_second == all_ts(pages), "continuation skipped messages"
    assert seen_first & seen_second == set(), "continuation repeated messages"
    assert page_of(second)["has_more"] is False


def test_a_truncation_never_reports_has_more_without_a_cursor(ctx_for, capsys):
    pages = synthetic_pages()  # twelve messages
    ctx = ctx_for(pages)
    _, complete = records(
        ctx, ["--channel", "#announcements", "--all-pages", "--max-messages", "12"], capsys
    )
    page = page_of(complete)
    assert page["truncated_by"] is None
    assert page["has_more"] is False
    assert page["next_cursor"] == ""

    _, cut = records(
        ctx, ["--channel", "#announcements", "--all-pages", "--max-messages", "10"], capsys
    )
    page = page_of(cut)
    assert page["truncated_by"] == "max-messages"
    assert page["has_more"] is True
    assert page["next_cursor"]


def test_the_request_limit_never_exceeds_the_remaining_budget(ctx_for, capsys):
    ctx = ctx_for(synthetic_pages())
    records(
        ctx,
        ["--channel", "#announcements", "--limit", "4", "--max-messages", "6", "--all-pages"],
        capsys,
    )
    limits = [params["limit"] for method, params in ctx.client.calls
              if method == "conversations.history"]
    assert limits == [4, 2]


# --- reply pagination under --threads full (gate fix 2) --------------------


def reply_pages(*groups):
    """Slack-shaped conversations.replies pages; every page but the last has a cursor."""
    pages = []
    for index, keys in enumerate(groups):
        last = index == len(groups) - 1
        pages.append({
            "ok": True,
            "messages": [MSG[k] for k in keys],
            "response_metadata": {"next_cursor": "" if last else f"r{index + 2}"},
        })
    return pages


TWO_REPLY_PAGES = (("thread_parent", "thread_reply"), ("thread_broadcast",))


def reply_calls(ctx):
    return [params for method, params in ctx.client.calls if method == "conversations.replies"]


def test_replies_follow_cursors_under_all_pages(ctx_for, capsys):
    ctx = ctx_for(history("thread_parent"), replies=reply_pages(*TWO_REPLY_PAGES))
    _, rows = records(
        ctx, ["--channel", "#announcements", "--threads", "full", "--all-pages"], capsys
    )
    assert [m["ts"] for m in messages_of(rows)] == [
        MSG["thread_parent"]["ts"], MSG["thread_reply"]["ts"], MSG["thread_broadcast"]["ts"]
    ]
    calls = reply_calls(ctx)
    assert len(calls) == 2
    assert calls[1]["cursor"] == "r2"
    assert page_of(rows)["incomplete_threads"] == 0


def test_replies_stop_after_one_page_without_all_pages_and_say_so(ctx_for, capsys):
    """A thread cut short must be counted, not silently shortened (invariants 4, 5)."""
    ctx = ctx_for(history("thread_parent"), replies=reply_pages(*TWO_REPLY_PAGES))
    warnings = []
    ctx.warn = warnings.append
    _, rows = records(ctx, ["--channel", "#announcements", "--threads", "full"], capsys)
    assert [m["ts"] for m in messages_of(rows)] == [
        MSG["thread_parent"]["ts"], MSG["thread_reply"]["ts"]
    ]
    assert len(reply_calls(ctx)) == 1
    assert page_of(rows)["incomplete_threads"] == 1
    assert any("not fully expanded" in w for w in warnings)


def test_a_fully_expanded_thread_reports_zero_incomplete(ctx_for, capsys):
    ctx = ctx_for(history("thread_parent"), replies=reply_pages(("thread_parent", "thread_reply")))
    warnings = []
    ctx.warn = warnings.append
    _, rows = records(ctx, ["--channel", "#announcements", "--threads", "full"], capsys)
    assert page_of(rows)["incomplete_threads"] == 0
    assert warnings == []


def test_the_text_page_line_mentions_unfinished_threads(ctx_for, capsys):
    ctx = ctx_for(history("thread_parent"), replies=reply_pages(*TWO_REPLY_PAGES), fmt="text")
    ctx.warn = lambda _m: None
    conversation_cmd.run(ctx, ["--channel", "#announcements", "--threads", "full"])
    ctx.flush()
    out = capsys.readouterr().out
    assert "1 thread not fully expanded" in out
    assert "--all-pages" in out


def test_replies_pages_use_the_limit_flag(ctx_for, capsys):
    ctx = ctx_for(history("thread_parent"), replies=reply_pages(*TWO_REPLY_PAGES))
    records(ctx, ["--channel", "#announcements", "--threads", "full", "--limit", "50"], capsys)
    assert reply_calls(ctx)[0]["limit"] == 50


# --- a broadcast reply is rendered once (gate fix 3) -----------------------


BROADCAST_TS = MSG["thread_broadcast"]["ts"]


def test_a_broadcast_reply_is_rendered_once_under_its_root(ctx_for, capsys):
    """Slack surfaces a thread_broadcast in channel history and in the thread's
    replies. Under --threads full both copies were emitted."""
    ctx = ctx_for(
        history("thread_parent", "thread_broadcast"),
        replies={"ok": True, "messages": [MSG["thread_parent"], MSG["thread_reply"],
                                          MSG["thread_broadcast"]]},
    )
    _, rows = records(ctx, ["--channel", "#announcements", "--threads", "full"], capsys)
    ts = [m["ts"] for m in messages_of(rows)]
    assert ts.count(BROADCAST_TS) == 1
    broadcast = next(m for m in messages_of(rows) if m["ts"] == BROADCAST_TS)
    assert broadcast["is_reply"] is True
    assert ts.index(MSG["thread_reply"]["ts"]) < ts.index(BROADCAST_TS)
    assert page_of(rows)["returned"] == 3


def test_a_broadcast_whose_root_is_outside_the_window_still_appears(ctx_for, capsys):
    ctx = ctx_for(history("thread_broadcast"), replies={"ok": True, "messages": []})
    _, rows = records(ctx, ["--channel", "#announcements", "--threads", "full"], capsys)
    assert [m["ts"] for m in messages_of(rows)] == [BROADCAST_TS]
    assert not [m for m, _ in ctx.client.calls if m == "conversations.replies"]


def test_a_broadcast_appears_once_without_thread_expansion(ctx_for, capsys):
    ctx = ctx_for(history("thread_parent", "thread_broadcast"))
    _, rows = records(ctx, ["--channel", "#announcements"], capsys)
    ts = [m["ts"] for m in messages_of(rows)]
    assert len(ts) == 2
    assert ts.count(BROADCAST_TS) == 1


# --- several conversations in one invocation (gate fix 6) ------------------


def test_several_selectors_read_several_conversations_with_a_header_and_page_each(ctx_for, capsys):
    """Pins behavior that already worked but was documented as not built.

    Repeatable --channel and --conversation read each match in turn, and each
    gets its own header and trailing page record so a reader can tell where one
    conversation ends and the next begins (P1, P2).
    """
    ctx = ctx_for(history("plain_text"))
    code, rows = records(
        ctx, ["--channel", "#announcements", "--channel", "#engineering"], capsys
    )
    assert code == 0
    assert [r["type"] for r in rows] == [
        "conversation", "message", "page", "conversation", "message", "page"
    ]
    headers = [r for r in rows if r["type"] == "conversation"]
    assert [h["id"] for h in headers] == ["C100", "C101"]
    pages = [r for r in rows if r["type"] == "page"]
    assert [p["conversation_id"] for p in pages] == ["C100", "C101"]


def test_the_audit_summary_lists_every_conversation_read(ctx_for, capsys):
    ctx = ctx_for(history("plain_text"))
    records(ctx, ["--channel", "#announcements", "--channel", "#engineering"], capsys)
    assert ctx.audit["conversations"] == ["C100", "C101"]


# --- review pass 1 findings --------------------------------------------------


def test_a_cursor_with_several_conversations_is_an_argument_error(ctx_for):
    """A Slack cursor belongs to one conversation. Applying it to two would
    resume the second from an unrelated offset and call the result complete.
    Never guessed (D8, invariant 6)."""
    from slackline import errors

    ctx = ctx_for(history("plain_text"))
    with pytest.raises(errors.ArgumentError) as caught:
        conversation_cmd.run(
            ctx, ["--channel", "#announcements", "--channel", "#engineering",
                  "--cursor", "abc"]
        )
    assert "cursor" in str(caught.value)
    assert not [m for m, _ in ctx.client.calls if m == "conversations.history"]


def test_a_broadcast_can_appear_on_both_sides_of_a_page_boundary(ctx_for, capsys):
    """Documented, not a defect. The exactness guarantee covers top-level
    history. A broadcast reply whose root falls on the next page is emitted from
    history on page one, because nothing else would show it, and again under its
    root on page two, because the dedup set is per invocation. Pinned so nobody
    later "fixes" it into a silent drop, which is the worse failure (D9)."""
    pages = [
        {"ok": True, "messages": [MSG["thread_broadcast"]],
         "response_metadata": {"next_cursor": "h2"}},
        {"ok": True, "messages": [MSG["thread_parent"]],
         "response_metadata": {"next_cursor": ""}},
    ]
    ctx = ctx_for(pages, replies={"ok": True, "messages": [
        MSG["thread_parent"], MSG["thread_reply"], MSG["thread_broadcast"]]})
    _, first = records(
        ctx, ["--channel", "#announcements", "--threads", "full", "--max-messages", "1"],
        capsys,
    )
    assert [m["ts"] for m in messages_of(first)] == [BROADCAST_TS]
    _, second = records(
        ctx, ["--channel", "#announcements", "--threads", "full", "--all-pages",
              "--cursor", page_of(first)["next_cursor"]],
        capsys,
    )
    assert BROADCAST_TS in [m["ts"] for m in messages_of(second)]


def test_filtered_replies_are_counted(ctx_for, capsys):
    """Invariant 5 applied to the reply path: a join inside a thread is dropped
    and the drop is reported."""
    ctx = ctx_for(history("thread_parent"), replies={"ok": True, "messages": [
        MSG["thread_parent"], MSG["channel_join"], MSG["thread_reply"]]})
    _, rows = records(ctx, ["--channel", "#announcements", "--threads", "full"], capsys)
    assert page_of(rows)["filtered"] == 1
    assert len(messages_of(rows)) == 2


def test_messages_without_a_ts_are_never_deduplicated_away(ctx_for, capsys):
    """Review pass 2: the dedup set must not treat two ts-less messages as one
    message. Real Slack always sends ts, but a malformed message is reported,
    never silently dropped (invariant 4)."""
    odd = [dict(MSG["plain_text"]), dict(MSG["mrkdwn_markup"])]
    for message in odd:
        message.pop("ts", None)
    ctx = ctx_for({"ok": True, "messages": odd, "response_metadata": {"next_cursor": ""}})
    _, rows = records(ctx, ["--channel", "#announcements"], capsys)
    assert page_of(rows)["returned"] == 2
    assert len(messages_of(rows)) == 2


# --- --order newest keeps replies beneath their root (gate re-run item 5) ---


def test_newest_first_keeps_replies_beneath_their_root(ctx_for, capsys):
    """Reversing the flat record list put every reply above its root. The
    order of root groups reverses; within a group the root stays first."""
    # Whichever of the two roots is newer must come first; the test does not assume.
    other = MSG["with_reactions_and_edit"]
    assert float(other["ts"]) != float(MSG["thread_parent"]["ts"])
    ctx = ctx_for(
        history("thread_parent", "with_reactions_and_edit"),
        replies={"ok": True, "messages": [MSG["thread_parent"], MSG["thread_reply"]]},
    )
    _, rows = records(
        ctx, ["--channel", "#announcements", "--threads", "full", "--order", "newest"], capsys
    )
    ts = [m["ts"] for m in messages_of(rows)]
    parent, reply = MSG["thread_parent"]["ts"], MSG["thread_reply"]["ts"]
    assert ts.index(reply) == ts.index(parent) + 1, "a reply sits right under its root"
    newest_root = max((MSG["thread_parent"]["ts"], other["ts"]), key=float)
    assert ts[0] == newest_root, "root groups are newest first"


def test_the_page_ceiling_is_announced_for_a_conversation(ctx_for, capsys):
    from slackline import transcript

    def endless(cursor=None, **_):
        n = int(cursor[1:]) + 1 if cursor else 1
        assert n <= 3 * transcript.MAX_PAGES, "collect did not stop; failing, not hanging"
        return {"ok": True, "messages": [MSG["plain_text"]] if n == 1 else [],
                "response_metadata": {"next_cursor": f"c{n}"}}

    ctx = ctx_for(endless)
    warnings = []
    ctx.warn = warnings.append
    _, rows = records(ctx, ["--channel", "#announcements", "--all-pages"], capsys)
    assert page_of(rows)["truncated_by"] == "max-pages"
    assert page_of(rows)["has_more"] is True
    assert any(f"{transcript.MAX_PAGES} pages" in w and "--cursor" in w for w in warnings)
