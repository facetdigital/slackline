"""``thread``: one root message and its replies, as a single coherent unit (D13, P4).

The distinction from ``conversation --threads full`` is worth stating, because
the two overlap and the wrong one gets reached for. ``conversation`` answers
"what happened in this channel over this window", and threads are expanded
inside that answer. ``thread`` answers "what was said in this specific
exchange", when the caller already has the ts, usually from a permalink or from
a previous read's output.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from slackline import cli_read, errors
from slackline.cache import Cache
from slackline.commands import thread as thread_cmd
from slackline.directory import Directory
from slackline.resolve import Resolver

DIR_FIXTURE = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "directory.json").read_text()
)
MSG = json.loads((pathlib.Path(__file__).parent / "fixtures" / "messages.json").read_text())

ROOT_TS = MSG["thread_parent"]["ts"]


def replies(*keys, cursor=""):
    return {
        "ok": True,
        "messages": [MSG[k] for k in keys],
        "has_more": bool(cursor),
        "response_metadata": {"next_cursor": cursor},
    }


@pytest.fixture
def ctx_for(tmp_path, fake_client):
    def build(reply_pages, fmt="jsonl"):
        responses = {
            "users.list": {"ok": True, "members": DIR_FIXTURE["users"]},
            "users.conversations": {"ok": True, "channels": DIR_FIXTURE["conversations"]},
            "conversations.replies": reply_pages,
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
    code = thread_cmd.run(ctx, list(argv))
    ctx.flush()
    out = capsys.readouterr().out
    return code, [json.loads(line) for line in out.strip().splitlines() if line]


def messages_of(rows):
    return [r for r in rows if r["type"] == "message"]


def page_of(rows):
    return next(r for r in rows if r["type"] == "page")


ARGV = ["--channel", "#announcements", "--ts", ROOT_TS]


# --- the thread renders as one coherent unit -------------------------------


def test_the_root_and_its_replies_come_back_together(ctx_for, capsys):
    ctx = ctx_for(replies("thread_parent", "thread_reply", "thread_broadcast"))
    code, rows = records(ctx, ARGV, capsys)
    assert code == 0
    assert [m["ts"] for m in messages_of(rows)] == [
        MSG["thread_parent"]["ts"],
        MSG["thread_reply"]["ts"],
        MSG["thread_broadcast"]["ts"],
    ]


def test_the_root_comes_first_even_when_slack_returns_it_out_of_order(ctx_for, capsys):
    """Order is the order the exchange happened, not the order of the payload."""
    ctx = ctx_for(replies("thread_reply", "thread_parent"))
    _, rows = records(ctx, ARGV, capsys)
    assert messages_of(rows)[0]["ts"] == MSG["thread_parent"]["ts"]


def test_the_root_is_not_marked_as_a_reply_and_the_replies_are(ctx_for, capsys):
    """The renderer indents replies under their root; it needs to be told which."""
    ctx = ctx_for(replies("thread_parent", "thread_reply"))
    _, rows = records(ctx, ARGV, capsys)
    root, reply = messages_of(rows)
    assert root["is_reply"] is False
    assert reply["is_reply"] is True


def test_the_root_appears_exactly_once(ctx_for, capsys):
    ctx = ctx_for(replies("thread_parent", "thread_reply"))
    _, rows = records(ctx, ARGV, capsys)
    assert [m["ts"] for m in messages_of(rows)].count(MSG["thread_parent"]["ts"]) == 1


def test_every_message_carries_the_thread_it_belongs_to(ctx_for, capsys):
    ctx = ctx_for(replies("thread_parent", "thread_reply"))
    _, rows = records(ctx, ARGV, capsys)
    assert all(m["thread_ts"] == ROOT_TS for m in messages_of(rows))


def test_mentions_are_named_and_content_is_rendered(ctx_for, capsys):
    """The whole point: a transcript a model can read (D9)."""
    ctx = ctx_for(replies("thread_parent", "thread_reply"))
    _, rows = records(ctx, ARGV, capsys)
    assert messages_of(rows)[1]["text"] == "Looks right to me."


def test_the_text_format_indents_replies_under_the_root(ctx_for, capsys):
    ctx = ctx_for(replies("thread_parent", "thread_reply"), fmt="text")
    thread_cmd.run(ctx, list(ARGV))
    ctx.flush()
    lines = capsys.readouterr().out.splitlines()
    root_line = next(line for line in lines if "Migration plan draft" in line)
    reply_line = next(line for line in lines if "Looks right to me." in line)
    assert not root_line.startswith(" ")
    assert reply_line.startswith("  ")


def test_a_trailing_page_record_reports_the_count(ctx_for, capsys):
    """Consistent with `conversation`: a reader can always tell what it got."""
    ctx = ctx_for(replies("thread_parent", "thread_reply"))
    _, rows = records(ctx, ARGV, capsys)
    assert page_of(rows)["returned"] == 2


def test_the_thread_is_identified_in_the_output(ctx_for, capsys):
    ctx = ctx_for(replies("thread_parent", "thread_reply"))
    _, rows = records(ctx, ARGV, capsys)
    conversation = next(r for r in rows if r["type"] == "conversation")
    assert conversation["thread_ts"] == ROOT_TS


# --- a ts that names no thread ---------------------------------------------


def test_a_ts_naming_no_thread_exits_5(ctx_for):
    """Slack says thread_not_found; that is a 5, not a generic API failure (D18)."""
    ctx = ctx_for(errors.NotFound("conversations.replies: thread_not_found"))
    with pytest.raises(errors.NotFound) as caught:
        thread_cmd.run(ctx, list(ARGV))
    assert caught.value.exit_code == 5


def test_an_empty_reply_payload_also_exits_5(ctx_for):
    """The failure mode that matters more, because it is silent.

    Slack can answer ok:true with no messages for a ts that is well-formed but
    names nothing. Emitting an empty transcript and exiting 0 would tell an
    agent the thread exists and is empty, which is a different and wrong fact.
    """
    ctx = ctx_for(replies())
    with pytest.raises(errors.NotFound) as caught:
        thread_cmd.run(ctx, list(ARGV))
    assert caught.value.exit_code == 5


# --- arguments -------------------------------------------------------------


def test_the_ts_is_required(ctx_for):
    ctx = ctx_for(replies("thread_parent"))
    with pytest.raises(errors.ArgumentError):
        thread_cmd.run(ctx, ["--channel", "#announcements"])


def test_a_time_window_is_refused_rather_than_ignored(ctx_for):
    """A thread is identified by its ts. Silently ignoring --since would let a
    caller believe a window was applied when none was."""
    ctx = ctx_for(replies("thread_parent"))
    with pytest.raises(errors.ArgumentError):
        thread_cmd.run(ctx, ARGV + ["--since", "48h"])


def test_an_unknown_option_is_refused(ctx_for):
    ctx = ctx_for(replies("thread_parent"))
    with pytest.raises(errors.ArgumentError):
        thread_cmd.run(ctx, ARGV + ["--threads", "full"])


def test_a_selector_matching_two_conversations_is_ambiguous(ctx_for):
    """A ts belongs to exactly one conversation, so two is unanswerable (D8)."""
    ctx = ctx_for(replies("thread_parent"))
    with pytest.raises(errors.AmbiguousSelector) as caught:
        thread_cmd.run(
            ctx, ["--channel", "#announcements", "--channel", "#engineering", "--ts", ROOT_TS]
        )
    assert caught.value.exit_code == 4


# --- bounded, like everything else -----------------------------------------


def test_a_long_thread_truncates_at_the_ceiling_and_still_exits_0(ctx_for, capsys):
    """D16 again: a bounded read that returned messages succeeded."""
    ctx = ctx_for(replies("thread_parent", "thread_reply", "thread_broadcast"))
    code, rows = records(ctx, ARGV + ["--max-messages", "2"], capsys)
    assert code == 0
    assert len(messages_of(rows)) == 2
    assert page_of(rows)["truncated_by"] == "max-messages"


def test_system_noise_is_filtered_and_counted(ctx_for, capsys):
    ctx = ctx_for(replies("thread_parent", "channel_join", "thread_reply"))
    _, rows = records(ctx, ARGV, capsys)
    assert len(messages_of(rows)) == 2
    assert page_of(rows)["filtered"] == 1


def test_the_call_goes_to_conversations_replies(ctx_for, capsys):
    """The read-only allowlist is the guarantee; this is the method it permits."""
    ctx = ctx_for(replies("thread_parent"))
    records(ctx, ARGV, capsys)
    assert [method for method, _ in ctx.client.calls if method.startswith("conversations.r")] == [
        "conversations.replies"
    ]


# --- continuation (D16, gate fix 1) ----------------------------------------


THREE = ("thread_parent", "thread_reply", "thread_broadcast")


def test_cursor_is_accepted_and_sent(ctx_for, capsys):
    ctx = ctx_for(replies(*THREE))
    _, rows = records(ctx, ARGV + ["--cursor", "fake:0:1"], capsys)
    call = next(params for method, params in ctx.client.calls
                if method == "conversations.replies")
    assert call["cursor"] == "fake:0:1"
    assert [m["ts"] for m in messages_of(rows)] == [
        MSG["thread_reply"]["ts"], MSG["thread_broadcast"]["ts"]
    ]


def test_a_truncated_thread_reports_an_exact_cursor(ctx_for, capsys):
    ctx = ctx_for(replies(*THREE))
    _, first = records(ctx, ARGV + ["--max-messages", "2"], capsys)
    page = page_of(first)
    assert page["truncated_by"] == "max-messages"
    assert page["has_more"] is True
    assert page["next_cursor"], "a truncated thread must hand back a way to continue"

    _, second = records(ctx, ARGV + ["--cursor", page["next_cursor"]], capsys)
    assert [m["ts"] for m in messages_of(second)] == [MSG["thread_broadcast"]["ts"]]
    assert page_of(second)["has_more"] is False


def test_a_thread_read_to_the_end_is_not_truncated(ctx_for, capsys):
    ctx = ctx_for(replies(*THREE))
    _, rows = records(ctx, ARGV + ["--max-messages", "3"], capsys)
    page = page_of(rows)
    assert page["truncated_by"] is None
    assert page["has_more"] is False
    assert page["next_cursor"] == ""


def test_continuation_pages_have_no_root_and_still_mark_replies(ctx_for, capsys):
    ctx = ctx_for(replies(*THREE))
    _, rows = records(ctx, ARGV + ["--cursor", "fake:0:1"], capsys)
    messages = messages_of(rows)
    assert messages
    assert all(m["ts"] != m["thread_ts"] for m in messages)
    assert all(m["is_reply"] is True for m in messages)
    header = next(r for r in rows if r["type"] == "conversation")
    assert header["thread_ts"] == ROOT_TS


# --- review pass 1 findings --------------------------------------------------


def test_all_pages_follows_reply_cursors(ctx_for, capsys):
    pages = [
        replies("thread_parent", "thread_reply", cursor="r2"),
        replies("thread_broadcast"),
    ]
    ctx = ctx_for(pages)
    _, rows = records(ctx, ARGV + ["--all-pages"], capsys)
    assert [m["ts"] for m in messages_of(rows)] == [
        MSG["thread_parent"]["ts"], MSG["thread_reply"]["ts"], MSG["thread_broadcast"]["ts"]
    ]
    calls = [p for m, p in ctx.client.calls if m == "conversations.replies"]
    assert len(calls) == 2 and calls[1]["cursor"] == "r2"
    assert page_of(rows)["has_more"] is False


def test_a_parent_repeated_on_a_continuation_page_is_dropped(ctx_for, capsys):
    """Whether Slack repeats the parent at the head of every replies page is not
    something the offline fake can know. If it does, a continuation page must
    not re-emit the root as a non-reply."""
    def by_cursor(cursor=None, **_):
        if cursor == "r2":
            return replies("thread_parent", "thread_broadcast")
        return replies("thread_parent", "thread_reply", cursor="r2")

    ctx = ctx_for(by_cursor)
    _, rows = records(ctx, ARGV + ["--cursor", "r2"], capsys)
    assert [m["ts"] for m in messages_of(rows)] == [MSG["thread_broadcast"]["ts"]]
    assert all(m["is_reply"] for m in messages_of(rows))
    assert page_of(rows)["returned"] == 1


# --- a reply's ts reads the thread from its root (gate fix 13) --------------


REPLY_TS = MSG["thread_reply"]["ts"]


def by_ts(ts=None, **_):
    """Slack answers a reply's ts with that one message, and a root's ts with
    the whole thread."""
    if ts == ROOT_TS:
        return replies("thread_parent", "thread_reply", "thread_broadcast")
    if ts == REPLY_TS:
        return replies("thread_reply")
    return replies()


REPLY_ARGV = ["--channel", "#announcements", "--ts", REPLY_TS]


def replies_calls(ctx):
    return [p["ts"] for m, p in ctx.client.calls if m == "conversations.replies"]


def test_a_reply_ts_reads_the_whole_thread_from_the_root(ctx_for, capsys):
    ctx = ctx_for(by_ts)
    ctx.warn = lambda _m: None
    _, rows = records(ctx, REPLY_ARGV, capsys)
    assert [m["ts"] for m in messages_of(rows)] == [
        ROOT_TS, REPLY_TS, MSG["thread_broadcast"]["ts"]
    ]
    header = next(r for r in rows if r["type"] == "conversation")
    assert header["thread_ts"] == ROOT_TS


def test_the_redirect_is_announced(ctx_for, capsys):
    ctx = ctx_for(by_ts)
    warnings = []
    ctx.warn = warnings.append
    records(ctx, REPLY_ARGV, capsys)
    assert any("names a reply" in w and ROOT_TS in w for w in warnings)


def test_the_redirect_makes_exactly_two_calls(ctx_for, capsys):
    ctx = ctx_for(by_ts)
    ctx.warn = lambda _m: None
    records(ctx, REPLY_ARGV, capsys)
    assert replies_calls(ctx) == [REPLY_TS, ROOT_TS]


def test_no_redirect_when_the_ts_is_the_root(ctx_for, capsys):
    ctx = ctx_for(by_ts)
    warnings = []
    ctx.warn = warnings.append
    records(ctx, ARGV, capsys)
    assert replies_calls(ctx) == [ROOT_TS]
    assert warnings == []


def test_no_redirect_on_a_continuation_page(ctx_for, capsys):
    ctx = ctx_for(by_ts)
    warnings = []
    ctx.warn = warnings.append
    records(ctx, REPLY_ARGV + ["--cursor", "r2"], capsys)
    assert replies_calls(ctx) == [REPLY_TS]
    assert warnings == []


def test_a_redirect_whose_root_returns_nothing_is_not_found(ctx_for):
    """A deleted root: the reply points at a ts Slack answers with nothing."""
    def gone_root(ts=None, **_):
        return replies("thread_reply") if ts == REPLY_TS else replies()

    ctx = ctx_for(gone_root)
    ctx.warn = lambda _m: None
    with pytest.raises(errors.NotFound) as caught:
        thread_cmd.run(ctx, list(REPLY_ARGV))
    assert REPLY_TS in str(caught.value) and ROOT_TS in str(caught.value)


def test_the_header_names_the_root_even_when_every_reply_is_filtered(ctx_for, capsys):
    """After a redirect the root is known; it must not be re-derived from a
    kept set that filtering may have emptied."""
    def noisy_thread(ts=None, **_):
        if ts == ROOT_TS:
            return replies("thread_parent", "channel_join", "channel_leave")
        return replies("thread_reply") if ts == REPLY_TS else replies()

    ctx = ctx_for(noisy_thread)
    ctx.warn = lambda _m: None
    _, rows = records(ctx, REPLY_ARGV, capsys)
    header = next(r for r in rows if r["type"] == "conversation")
    assert header["thread_ts"] == ROOT_TS
    root = messages_of(rows)[0]
    assert root["ts"] == ROOT_TS and root["is_reply"] is False
    assert page_of(rows)["thread_ts"] == ROOT_TS


def test_a_filtered_reply_ts_still_reads_the_thread_from_its_root(ctx_for, capsys):
    """The redirect must not depend on the reply surviving the filter. A bot
    reply's ts under --exclude-bots is still a reply, and its root is still
    the thread."""
    bot_reply = dict(MSG["thread_reply"], subtype="bot_message", bot_id="B1")

    def by_ts_with_bot(ts=None, **_):
        if ts == ROOT_TS:
            return {"ok": True, "messages": [MSG["thread_parent"], bot_reply,
                                              MSG["thread_broadcast"]],
                    "response_metadata": {"next_cursor": ""}}
        if ts == REPLY_TS:
            return {"ok": True, "messages": [bot_reply],
                    "response_metadata": {"next_cursor": ""}}
        return replies()

    ctx = ctx_for(by_ts_with_bot)
    ctx.warn = lambda _m: None
    _, rows = records(ctx, REPLY_ARGV + ["--exclude-bots"], capsys)
    header = next(r for r in rows if r["type"] == "conversation")
    assert header["thread_ts"] == ROOT_TS
    assert [m["ts"] for m in messages_of(rows)] == [ROOT_TS, MSG["thread_broadcast"]["ts"]]
    assert page_of(rows)["filtered"] == 1


def test_the_ceiling_is_literal_on_the_first_page(ctx_for, capsys):
    """Slack counts the parent outside `limit`, so the first request asks for
    one fewer reply and --max-messages N yields exactly N messages."""
    ctx = ctx_for(replies(*THREE))
    _, rows = records(ctx, ARGV + ["--max-messages", "2"], capsys)
    assert len(messages_of(rows)) == 2
    first_call = next(p for m, p in ctx.client.calls if m == "conversations.replies")
    assert first_call["limit"] == 1


def test_the_thread_page_record_has_the_shared_shape(ctx_for, capsys):
    """conversation and thread must not drift into two page contracts."""
    ctx = ctx_for(replies("thread_parent", "thread_reply"))
    _, rows = records(ctx, ARGV, capsys)
    page = page_of(rows)
    assert page["incomplete_threads"] == 0
    assert set(page) == {"type", "conversation_id", "returned", "filtered",
                         "incomplete_threads", "has_more", "next_cursor",
                         "truncated_by", "thread_ts"}


def test_a_ceiling_of_one_yields_the_root_and_one_reply(ctx_for, capsys):
    """The reserve cannot take the request below one reply (a non-positive
    limit must never reach the wire), so --max-messages 1 is the documented
    exception: the root plus one reply."""
    ctx = ctx_for(replies(*THREE))
    _, rows = records(ctx, ARGV + ["--max-messages", "1"], capsys)
    assert [m["ts"] for m in messages_of(rows)] == [ROOT_TS, MSG["thread_reply"]["ts"]]
    assert page_of(rows)["truncated_by"] == "max-messages"


def test_the_page_ceiling_is_announced_for_a_thread(ctx_for, capsys):
    from slackline import transcript

    def endless(cursor=None, **_):
        n = int(cursor[1:]) + 1 if cursor else 1
        assert n <= 3 * transcript.MAX_PAGES, "collect did not stop; failing, not hanging"
        return {"ok": True, "messages": [MSG["thread_parent"]] if n == 1 else [],
                "response_metadata": {"next_cursor": f"c{n}"}}

    ctx = ctx_for(endless)
    warnings = []
    ctx.warn = warnings.append
    _, rows = records(ctx, ARGV + ["--all-pages"], capsys)
    assert page_of(rows)["truncated_by"] == "max-pages"
    assert any(f"{transcript.MAX_PAGES} pages" in w and "--cursor" in w for w in warnings)


def test_the_redirect_read_also_reserves_for_the_parent(ctx_for, capsys):
    """thread --ts <reply> --max-messages N returns exactly N: the redirect's
    request asks for N-1 replies like the direct read does."""
    ctx = ctx_for(by_ts)
    ctx.warn = lambda _m: None
    _, rows = records(ctx, REPLY_ARGV + ["--max-messages", "2"], capsys)
    assert len(messages_of(rows)) == 2
    limits = [p["limit"] for m, p in ctx.client.calls if m == "conversations.replies"]
    assert limits[-1] == 1
