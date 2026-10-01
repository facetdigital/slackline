"""Continuation exercised through ``main()`` with a canned transport (D16, D21).

The command-level tests drive the command function directly. This one goes in
through argv, credential resolution, the real ``SlackClient``, and the formatter,
so the ``--cursor`` flag and the ``page`` record are tested as an agent would use
them. No socket is opened: the urllib transport is replaced.
"""
from __future__ import annotations

import json
import urllib.parse

import pytest

from slackline import api, cli_read

USERS = [
    {"id": "U1", "name": "me", "profile": {"real_name": "Me"}},
    {"id": "U2", "name": "alpha", "profile": {"real_name": "Alpha One"}},
]
CONVERSATIONS = [{"id": "C1", "name": "general", "is_channel": True}]


def message(i):
    return {"type": "message", "user": "U2", "text": f"m{i}", "ts": f"{1000 - i}.000000"}


# Three pages of four, keyed by the cursor that fetches them.
PAGES = {
    "": ([message(i) for i in range(0, 4)], "c2"),
    "c2": ([message(i) for i in range(4, 8)], "c3"),
    "c3": ([message(i) for i in range(8, 12)], ""),
}
EVERY_TS = {m["ts"] for items, _ in PAGES.values() for m in items}


def serve_history(query):
    """Honor cursor and limit the way Slack does, including a mid-page cursor."""
    cursor = query.get("cursor", "")
    offset = 0
    if cursor.startswith("mid:"):
        _, cursor, offset = cursor.split(":")
        offset = int(offset)
    items, following = PAGES[cursor]
    limit = int(query.get("limit", 200))
    window = items[offset:offset + limit]
    end = offset + len(window)
    next_cursor = f"mid:{cursor}:{end}" if end < len(items) else following
    return {"ok": True, "messages": window, "response_metadata": {"next_cursor": next_cursor}}


def canned_transport(url, headers, timeout):
    assert headers["Authorization"].startswith("Bearer xoxp-")
    path = url[len(api.BASE_URL):]
    method, _, query_string = path.partition("?")
    query = dict(urllib.parse.parse_qsl(query_string))
    ok = api.Headers({"content-type": "application/json"})
    if method == "auth.test":
        return api.Response(200, {"ok": True, "user_id": "U1", "user": "me",
                                  "team_id": "T1", "team": "Team"}, ok)
    if method == "users.list":
        return api.Response(200, {"ok": True, "members": USERS}, ok)
    if method == "users.conversations":
        return api.Response(200, {"ok": True, "channels": CONVERSATIONS}, ok)
    if method == "conversations.history":
        return api.Response(200, serve_history(query), ok)
    raise AssertionError(f"unexpected method {method}")


@pytest.fixture
def offline(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "_urllib_transport", canned_transport)
    monkeypatch.setenv("SLACK_USER_TOKEN", "xoxp-test-1-2-3")
    monkeypatch.setenv("SLACKLINE_AUDIT_LOG", "none")
    monkeypatch.setenv("SLACKLINE_CACHE_DIR", str(tmp_path / "cache"))


def run(capsys, *argv):
    code = cli_read.main(list(argv))
    out = capsys.readouterr().out
    rows = [json.loads(line) for line in out.splitlines() if line.strip()]
    return code, rows


def test_a_truncated_read_continues_with_its_cursor_and_loses_nothing(offline, capsys):
    code, first = run(capsys, "conversation", "--channel", "general",
                      "--max-messages", "2", "--format", "jsonl")
    assert code == 0, "a truncated read is a successful read (D16)"
    page = next(r for r in first if r["type"] == "page")
    assert page["truncated_by"] == "max-messages"
    assert page["has_more"] is True
    assert page["next_cursor"]

    code, second = run(capsys, "conversation", "--channel", "general",
                       "--cursor", page["next_cursor"], "--all-pages",
                       "--max-messages", "100", "--format", "jsonl")
    assert code == 0
    got_first = {r["ts"] for r in first if r["type"] == "message"}
    got_second = {r["ts"] for r in second if r["type"] == "message"}
    assert got_first | got_second == EVERY_TS, "continuation skipped messages"
    assert got_first & got_second == set(), "continuation repeated messages"


def test_a_cut_on_the_last_page_still_hands_back_a_cursor(offline, capsys):
    code, rows = run(capsys, "conversation", "--channel", "general", "--all-pages",
                     "--max-messages", "10", "--format", "jsonl")
    assert code == 0
    page = next(r for r in rows if r["type"] == "page")
    assert page["has_more"] is True
    assert page["next_cursor"], "has_more without a cursor is a promise that cannot be kept"
    assert len([r for r in rows if r["type"] == "message"]) == 10


def test_reading_everything_reports_no_more(offline, capsys):
    code, rows = run(capsys, "conversation", "--channel", "general", "--all-pages",
                     "--format", "jsonl")
    assert code == 0
    page = next(r for r in rows if r["type"] == "page")
    assert page["has_more"] is False
    assert page["next_cursor"] == ""
    assert page["truncated_by"] is None
    assert {r["ts"] for r in rows if r["type"] == "message"} == EVERY_TS
