"""The HTTP layer, and the allowlist that makes 'read-only' a fact (D3, D17)."""
from __future__ import annotations

import json
import pytest

from slackline import api, errors


class RecordingTransport:
    """Stands in for urllib so tests never touch the network (D21)."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append({"url": url, "headers": headers, "timeout": timeout})
        if not self.responses:
            raise AssertionError("transport called more times than expected")
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def make_client(responses):
    transport = RecordingTransport(responses)
    client = api.SlackClient(token="xoxp-test-token", transport=transport)
    return client, transport


def ok(payload):
    return api.Response(status=200, body=dict(payload, ok=True), headers={})


# --- The allowlist ---------------------------------------------------------


@pytest.mark.parametrize(
    "method",
    [
        "chat.postMessage",
        "chat.delete",
        "chat.update",
        "reactions.add",
        "conversations.open",
        "conversations.join",
        "conversations.mark",
        "files.upload",
        "admin.users.remove",
    ],
)
def test_write_methods_are_refused_before_any_io(method):
    """The refusal happens before a socket is opened, not after Slack says no."""
    client, transport = make_client([])
    with pytest.raises(errors.ForbiddenMethod) as caught:
        client.call(method)
    assert caught.value.exit_code == 7
    assert transport.calls == [], "no request may be attempted for a banned method"


def test_conversations_open_is_banned_despite_being_useful():
    """D6: it would resolve group DMs neatly, but it can create one."""
    assert "conversations.open" not in api.READ_ONLY_METHODS


def test_conversations_mark_is_banned():
    """Reading must not change what the human sees as unread."""
    assert "conversations.mark" not in api.READ_ONLY_METHODS


def test_allowlisted_method_is_called():
    client, transport = make_client([ok({"user": "sam"})])
    body = client.call("auth.test")
    assert body["user"] == "sam"
    assert transport.calls[0]["url"].startswith("https://slack.com/api/auth.test")


# --- Request construction --------------------------------------------------


def test_parameters_are_encoded_and_none_values_dropped():
    client, transport = make_client([ok({"messages": []})])
    client.call("conversations.history", channel="C1", limit=200, cursor=None)
    url = transport.calls[0]["url"]
    assert "channel=C1" in url
    assert "limit=200" in url
    assert "cursor" not in url, "None parameters must be omitted, not sent empty"


def test_token_travels_in_the_authorization_header_only():
    client, transport = make_client([ok({})])
    client.call("auth.test")
    call = transport.calls[0]
    assert call["headers"]["Authorization"] == "Bearer xoxp-test-token"
    assert "xoxp-test-token" not in call["url"], "never put a token in a URL"


# --- Error handling --------------------------------------------------------


def test_slack_level_failure_becomes_an_api_error():
    client, _ = make_client([api.Response(200, {"ok": False, "error": "channel_not_found"}, {})])
    with pytest.raises(errors.NotFound):
        client.call("conversations.history", channel="C1")


def test_invalid_auth_becomes_an_auth_error_with_exit_three():
    client, _ = make_client([api.Response(200, {"ok": False, "error": "invalid_auth"}, {})])
    with pytest.raises(errors.AuthError) as caught:
        client.call("auth.test")
    assert caught.value.exit_code == 3


def test_missing_scope_reports_which_scope_is_missing():
    client, _ = make_client(
        [api.Response(200, {"ok": False, "error": "missing_scope", "needed": "search:read"}, {})]
    )
    with pytest.raises(errors.AuthError) as caught:
        client.call("conversations.history", channel="C1")
    assert "search:read" in str(caught.value)


def test_error_text_never_contains_the_token():
    client, _ = make_client([api.Response(200, {"ok": False, "error": "invalid_auth"}, {})])
    with pytest.raises(errors.AuthError) as caught:
        client.call("auth.test")
    assert "xoxp-test-token" not in caught.value.report()


# --- Retry policy (D17) ----------------------------------------------------


def test_rate_limit_honors_retry_after_exactly():
    slept = []
    client, _ = make_client(
        [
            api.Response(429, {"ok": False, "error": "ratelimited"}, {"Retry-After": "7"}),
            ok({"messages": []}),
        ]
    )
    client.sleep = slept.append
    client.call("conversations.history", channel="C1")
    assert slept == [7.0], "Retry-After must be honored exactly, not approximated"


def test_server_errors_back_off_exponentially_and_are_capped():
    slept = []
    client, _ = make_client(
        [api.Response(500, {}, {}), api.Response(502, {}, {}), ok({"messages": []})]
    )
    client.sleep = slept.append
    client.jitter = lambda: 0.0
    client.call("conversations.history", channel="C1")
    assert len(slept) == 2
    assert slept[0] < slept[1], "backoff must grow"
    assert all(s <= api.MAX_BACKOFF_SECONDS for s in slept)


def test_retries_are_capped_and_then_give_up():
    client, _ = make_client([api.Response(500, {}, {})] * 10)
    client.sleep = lambda _s: None
    with pytest.raises(errors.SlackApiError):
        client.call("conversations.history", channel="C1")


def test_a_successful_first_try_never_sleeps():
    slept = []
    client, _ = make_client([ok({})])
    client.sleep = slept.append
    client.call("auth.test")
    assert slept == []


# --- Pagination ------------------------------------------------------------


def test_paginate_follows_cursors_until_exhausted():
    client, transport = make_client(
        [
            api.Response(200, {"ok": True, "messages": [1, 2],
                               "response_metadata": {"next_cursor": "abc"}}, {}),
            api.Response(200, {"ok": True, "messages": [3], "response_metadata": {"next_cursor": ""}}, {}),
        ]
    )
    pages = list(client.paginate("conversations.history", "messages", channel="C1"))
    assert [p.items for p in pages] == [[1, 2], [3]]
    assert "cursor=abc" in transport.calls[1]["url"]
    assert pages[-1].next_cursor == ""


def test_paginate_stops_at_the_page_budget_and_reports_continuation():
    """D15/D16: bounded by default, and the caller is told there is more."""
    client, _ = make_client(
        [
            api.Response(200, {"ok": True, "messages": [1],
                               "response_metadata": {"next_cursor": "c1"}}, {}),
            api.Response(200, {"ok": True, "messages": [2],
                               "response_metadata": {"next_cursor": "c2"}}, {}),
        ]
    )
    pages = list(client.paginate("conversations.history", "messages", max_pages=2, channel="C1"))
    assert len(pages) == 2
    assert pages[-1].next_cursor == "c2", "continuation must survive the budget stop"


# --- Retry-After has a ceiling (gate fix 9) --------------------------------


def test_an_absurd_retry_after_is_refused_rather_than_slept():
    """An intermediary must not be able to park an agent for as long as it likes."""
    slept = []
    client, _ = make_client(
        [api.Response(429, {"ok": False, "error": "ratelimited"}, {"Retry-After": "900"})]
    )
    client.sleep = slept.append
    with pytest.raises(errors.SlackApiError) as caught:
        client.call("conversations.history", channel="C1")
    assert "900" in str(caught.value) and "ceiling" in str(caught.value)
    assert slept == []


def test_a_retry_after_within_the_ceiling_is_still_honored_exactly():
    slept = []
    client, _ = make_client(
        [api.Response(429, {"ok": False, "error": "ratelimited"}, {"Retry-After": "120"}),
         ok({"messages": []})]
    )
    client.sleep = slept.append
    client.call("conversations.history", channel="C1")
    assert slept == [120.0]


def test_the_ceiling_is_a_named_constant():
    assert api.MAX_RETRY_AFTER_SECONDS == 300


def test_a_negative_retry_after_does_not_crash():
    """time.sleep(-1) raises; a hostile header must not turn into exit 1."""
    slept = []
    client, _ = make_client(
        [api.Response(429, {"ok": False, "error": "ratelimited"}, {"Retry-After": "-5"}),
         ok({"messages": []})]
    )
    client.sleep = slept.append
    client.call("conversations.history", channel="C1")
    assert slept == [0.0]


# --- paginate has a ceiling too (security pass on the gate re-run PR) --------


def test_paginate_stops_at_a_default_page_ceiling_and_announces_it():
    """The directory and membership loops had no ceiling; a server returning a
    cursor with nothing behind it could hold every command through users.list."""
    def endless(url, headers, timeout):
        n = endless.calls = getattr(endless, "calls", 0) + 1
        assert n <= api.MAX_PAGES_PER_CALL * 3, "paginate did not stop"
        return api.Response(200, {"ok": True, "members": [],
                                  "response_metadata": {"next_cursor": f"c{n}"}}, api.Headers())

    waits = []
    client = api.SlackClient(token="xoxp-t", transport=endless, on_wait=waits.append)
    pages = list(client.paginate("users.list", "members", limit=200))
    assert len(pages) == api.MAX_PAGES_PER_CALL
    assert pages[-1].next_cursor, "the continuation is still reported"
    assert any("pages" in w for w in waits)


def test_an_explicit_max_pages_below_the_ceiling_still_wins():
    responses = [api.Response(200, {"ok": True, "members": [1],
                                    "response_metadata": {"next_cursor": "c"}}, api.Headers())] * 5
    client = api.SlackClient(token="xoxp-t", transport=lambda *a: responses.pop(0))
    assert len(list(client.paginate("users.list", "members", max_pages=2))) == 2
