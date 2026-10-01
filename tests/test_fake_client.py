"""The test fake must page the way Slack does (D21).

The transcript commands drive ``client.call()`` themselves with a per-request
``limit`` and follow the cursor Slack returns. A fake that ignores ``limit`` or
cannot resume mid-page would let a test pass against exactly the continuation bug
the real client exposes, so the fake's paging contract is pinned here.
"""
from __future__ import annotations

import pytest

from slackline import api, errors


def msg(i):
    return {"type": "message", "text": f"m{i}", "ts": f"{1000 - i}.000000"}


PAGE_A = {
    "ok": True,
    "messages": [msg(1), msg(2), msg(3)],
    "response_metadata": {"next_cursor": "real2"},
}
PAGE_B = {"ok": True, "messages": [msg(4), msg(5)], "response_metadata": {"next_cursor": ""}}


@pytest.fixture
def client(fake_client):
    return fake_client({"conversations.history": [PAGE_A, PAGE_B]})


def texts(body):
    return [m["text"] for m in body["messages"]]


def test_call_serves_at_most_limit_items(client):
    body = client.call("conversations.history", limit=2)
    assert texts(body) == ["m1", "m2"]
    cursor = body["response_metadata"]["next_cursor"]
    assert cursor and cursor != "real2", "a cut page must not hand out the next page's cursor"


def test_a_mid_page_cursor_resumes_without_skipping_or_repeating(client):
    first = client.call("conversations.history", limit=2)
    cursor = first["response_metadata"]["next_cursor"]
    second = client.call("conversations.history", cursor=cursor, limit=2)
    assert texts(second) == ["m3"]
    assert second["response_metadata"]["next_cursor"] == "real2"
    third = client.call("conversations.history", cursor="real2", limit=2)
    assert texts(third) == ["m4", "m5"]
    assert third["response_metadata"]["next_cursor"] == ""


def test_a_single_dict_page_is_also_sliced(fake_client):
    # conversations.history, not replies: replies pages carry the parent in
    # addition to limit (see the tests at the end of this file).
    client = fake_client({"conversations.history": dict(PAGE_A, response_metadata={"next_cursor": ""})})
    body = client.call("conversations.history", limit=1)
    assert texts(body) == ["m1"]
    assert body["response_metadata"]["next_cursor"]


def test_no_limit_serves_the_whole_page(client):
    body = client.call("conversations.history")
    assert texts(body) == ["m1", "m2", "m3"]
    assert body["response_metadata"]["next_cursor"] == "real2"


def test_an_unknown_cursor_is_an_assertion_error(client):
    with pytest.raises(AssertionError):
        client.call("conversations.history", cursor="nope")


def test_a_page_without_a_list_is_returned_unchanged(fake_client):
    client = fake_client({"auth.test": {"ok": True, "user_id": "U1"}})
    assert client.call("auth.test", limit=5) == {"ok": True, "user_id": "U1"}


def test_exceptions_and_callables_still_work(fake_client):
    client = fake_client(
        {
            "conversations.replies": errors.NotFound("thread_not_found"),
            "conversations.members": lambda channel=None, **_: {
                "ok": True, "members": [channel], "response_metadata": {"next_cursor": ""}
            },
        }
    )
    with pytest.raises(errors.NotFound):
        client.call("conversations.replies", ts="1")
    assert client.call("conversations.members", channel="G1")["members"] == ["G1"]


def test_calls_are_recorded_with_their_params(client):
    client.call("conversations.history", limit=2, channel="C1")
    assert client.calls[-1] == ("conversations.history", {"limit": 2, "channel": "C1"})


def test_the_allowlist_is_still_enforced(client):
    with pytest.raises(errors.ForbiddenMethod):
        client.call("chat.postMessage", limit=1)


def test_paginate_is_unchanged_for_directory_calls(fake_client):
    client = fake_client(
        {
            "users.list": [
                {"ok": True, "members": [1, 2], "response_metadata": {"next_cursor": "u2"}},
                {"ok": True, "members": [3], "response_metadata": {"next_cursor": ""}},
            ]
        }
    )
    pages = list(client.paginate("users.list", "members", limit=200))
    assert [p.items for p in pages] == [[1, 2], [3]]
    assert isinstance(pages[0], api.Page)


# --- review pass 1: the fake fails loudly, never confusingly ------------------


@pytest.mark.parametrize("cursor", ["fake:9:0", "fake:x:y", "fake:0"])
def test_a_malformed_or_out_of_range_fake_cursor_is_an_assertion_error(client, cursor):
    with pytest.raises(AssertionError):
        client.call("conversations.history", cursor=cursor)


def test_a_page_with_two_lists_is_refused_rather_than_guessed(fake_client):
    client = fake_client({"conversations.history": {
        "ok": True, "warnings": ["x"], "messages": [msg(1)],
        "response_metadata": {"next_cursor": ""}}})
    with pytest.raises(AssertionError):
        client.call("conversations.history", limit=1)


def test_a_callable_payload_is_served_as_the_page_for_that_call(fake_client):
    """A callable already saw the cursor; its result is the page, sliced from 0."""
    def by_cursor(cursor=None, **_):
        return {"ok": True, "messages": [msg(1), msg(2)] if cursor else [msg(0)],
                "response_metadata": {"next_cursor": "" if cursor else "n2"}}
    client = fake_client({"conversations.replies": by_cursor})
    first = client.call("conversations.replies", limit=5)
    assert texts(first) == ["m0"] and first["response_metadata"]["next_cursor"] == "n2"
    second = client.call("conversations.replies", cursor="n2", limit=5)
    assert texts(second) == ["m1", "m2"]


def test_paginate_honors_exclude_archived_like_slack(fake_client):
    """Without this the inventory tests could not tell whether archived rows
    were fetched or merely filtered locally."""
    rows = [{"id": "C1", "is_archived": False}, {"id": "C2", "is_archived": True}]
    client = fake_client({"users.conversations": {"ok": True, "channels": rows,
                                                  "response_metadata": {"next_cursor": ""}}})
    hidden = list(client.paginate("users.conversations", "channels", exclude_archived="true"))
    assert [c["id"] for c in hidden[0].items] == ["C1"]
    shown = list(client.paginate("users.conversations", "channels", exclude_archived=False))
    assert [c["id"] for c in shown[0].items] == ["C1", "C2"]


# --- replies pages carry the parent in addition to limit (gate re-run item 6) --


def replies_payload():
    parent = dict(msg(0), thread_ts=msg(0)["ts"])
    return {"ok": True, "messages": [parent, msg(1), msg(2), msg(3)],
            "response_metadata": {"next_cursor": ""}}


def test_replies_pages_carry_the_parent_in_addition_to_limit(fake_client):
    """Observed live on 2026-08-30: conversations.replies returns the parent
    plus `limit` replies on the first page, and only replies on cursor pages."""
    client = fake_client({"conversations.replies": replies_payload()})
    first = client.call("conversations.replies", limit=2)
    assert texts(first) == ["m0", "m1", "m2"]
    cursor = first["response_metadata"]["next_cursor"]
    assert cursor
    second = client.call("conversations.replies", cursor=cursor, limit=2)
    assert texts(second) == ["m3"]
    assert second["response_metadata"]["next_cursor"] == ""


def test_history_pages_are_not_affected(fake_client):
    client = fake_client({"conversations.history": replies_payload()})
    first = client.call("conversations.history", limit=2)
    assert texts(first) == ["m0", "m1"]


def test_a_cursor_page_of_replies_carries_no_parent(fake_client):
    """The parent rides only on the first page; a page fetched by cursor is
    replies only, so the rule keys on the cursor, not on the slice offset."""
    parent = dict(msg(0), thread_ts=msg(0)["ts"])
    pages = [
        {"ok": True, "messages": [parent, msg(1)], "response_metadata": {"next_cursor": "r2"}},
        {"ok": True, "messages": [msg(2), msg(3), msg(4)], "response_metadata": {"next_cursor": ""}},
    ]
    client = fake_client({"conversations.replies": pages})
    second = client.call("conversations.replies", cursor="r2", limit=2)
    assert texts(second) == ["m2", "m3"]
