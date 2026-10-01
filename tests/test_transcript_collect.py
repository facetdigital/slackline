"""The bounded collector behind ``conversation`` and ``thread`` (D15, D16).

Each request asks Slack for exactly the remaining budget, so the ceiling always
coincides with a page boundary and the cursor Slack returns resumes at the first
undelivered message. That is what makes ``--cursor`` continuation exact: nothing
is skipped and nothing is repeated.
"""
from __future__ import annotations

from slackline.transcript import collect


def msg(i, subtype=None):
    record = {"type": "message", "user": "U002", "text": f"m{i}", "ts": f"{1000 - i}.000000"}
    if subtype:
        record["subtype"] = subtype
    return record


def pages(*groups):
    """Build Slack-shaped pages; every page but the last carries a cursor."""
    built = []
    for index, group in enumerate(groups):
        cursor = f"p{index + 1}" if index < len(groups) - 1 else ""
        built.append({"ok": True, "messages": list(group),
                      "response_metadata": {"next_cursor": cursor}})
    return built


def keep(message):
    return message.get("subtype") != "channel_join"


def limits_sent(client):
    return [params["limit"] for method, params in client.calls
            if method == "conversations.history"]


def run(client, **overrides):
    options = dict(limit=4, max_messages=None, all_pages=True, cursor=None, keep=keep,
                   channel="C1")
    options.update(overrides)
    return collect(client, "conversations.history", "messages", **options)


THREE_PAGES = pages([msg(0), msg(1), msg(2), msg(3)],
                    [msg(4), msg(5), msg(6), msg(7)],
                    [msg(8), msg(9), msg(10), msg(11)])


def test_each_request_asks_for_the_remaining_budget(fake_client):
    client = fake_client({"conversations.history": THREE_PAGES})
    result = run(client, max_messages=6)
    assert limits_sent(client) == [4, 2]
    assert len(result.messages) == 6
    assert result.truncated_by == "max-messages"
    assert result.next_cursor


def test_continuing_from_a_truncation_skips_nothing_and_repeats_nothing(fake_client):
    client = fake_client({"conversations.history": THREE_PAGES})
    first = run(client, max_messages=6)
    second = run(client, max_messages=100, cursor=first.next_cursor)
    seen_first = {m["ts"] for m in first.messages}
    seen_second = {m["ts"] for m in second.messages}
    everything = {m["ts"] for page in THREE_PAGES for m in page["messages"]}
    assert seen_first | seen_second == everything, "a message was skipped"
    assert seen_first & seen_second == set(), "a message was repeated"
    assert second.next_cursor == ""
    assert second.truncated_by is None


def test_a_ceiling_reached_on_the_last_page_is_not_a_truncation(fake_client):
    ten = pages([msg(0), msg(1), msg(2), msg(3)],
                [msg(4), msg(5), msg(6), msg(7)],
                [msg(8), msg(9)])
    client = fake_client({"conversations.history": ten})
    result = run(client, max_messages=10)
    assert len(result.messages) == 10
    assert result.truncated_by is None
    assert result.next_cursor == ""


def test_filtered_messages_use_page_slots_but_not_budget(fake_client):
    client = fake_client({"conversations.history": pages(
        [msg(0, "channel_join"), msg(1, "channel_join"), msg(2), msg(3)],
        [msg(4), msg(5)],
    )})
    result = run(client, max_messages=2)
    assert limits_sent(client) == [2, 2], "the budget is unspent until something is kept"
    assert [m["text"] for m in result.messages] == ["m2", "m3"]
    assert result.filtered == 2
    assert result.truncated_by == "max-messages"


def test_single_page_mode_stops_after_one_request_with_the_cursor(fake_client):
    client = fake_client({"conversations.history": THREE_PAGES})
    result = run(client, max_messages=100, all_pages=False)
    assert len(client.calls) == 1
    assert len(result.messages) == 4
    assert result.truncated_by is None
    assert result.next_cursor == "p1"


def test_single_page_mode_reports_truncation_when_the_budget_is_spent(fake_client):
    client = fake_client({"conversations.history": THREE_PAGES})
    result = run(client, max_messages=2, all_pages=False)
    assert len(client.calls) == 1
    assert result.truncated_by == "max-messages"
    assert result.next_cursor


def test_seen_counts_kept_and_filtered(fake_client):
    client = fake_client({"conversations.history": pages(
        [msg(0, "channel_join"), msg(1), msg(2)])})
    result = run(client)
    assert result.seen == 3
    assert result.seen == len(result.messages) + result.filtered


def test_no_budget_means_page_size_is_the_limit(fake_client):
    client = fake_client({"conversations.history": THREE_PAGES})
    result = run(client, max_messages=None, limit=4)
    assert limits_sent(client) == [4, 4, 4]
    assert len(result.messages) == 12
    assert result.truncated_by is None
    assert result.pages == 3


def test_extra_params_reach_every_request(fake_client):
    client = fake_client({"conversations.history": THREE_PAGES})
    run(client, oldest="1.000000", latest="2.000000")
    for _, params in client.calls:
        assert params["channel"] == "C1"
        assert params["oldest"] == "1.000000" and params["latest"] == "2.000000"


def test_a_starting_cursor_is_sent_on_the_first_request(fake_client):
    client = fake_client({"conversations.history": THREE_PAGES})
    run(client, cursor="p1", all_pages=False)
    assert client.calls[0][1]["cursor"] == "p1"


def test_an_empty_first_page_is_reported_as_nothing_seen(fake_client):
    client = fake_client({"conversations.history": pages([])})
    result = run(client)
    assert result.seen == 0
    assert result.messages == []
    assert result.next_cursor == ""


# --- the loop must stay bounded even if Slack misbehaves (review pass 1) ---


class OverDeliveringClient:
    """Returns one more item than asked for, forever, always with a cursor."""

    def __init__(self):
        self.calls = []
        self.counter = 0

    def call(self, method, **params):
        self.calls.append((method, params))
        limit = params["limit"]
        items = [msg(self.counter + k) for k in range(limit + 1)]
        self.counter += limit + 1
        return {"ok": True, "messages": items, "response_metadata": {"next_cursor": "more"}}


def test_an_over_delivering_page_cannot_break_the_ceiling():
    client = OverDeliveringClient()
    result = run(client, max_messages=4, limit=4)
    assert len(result.messages) <= 4 + 1, "at most one page's overshoot can ever land"
    assert result.truncated_by == "max-messages"
    assert result.next_cursor == "more"
    assert all(params["limit"] >= 1 for _, params in client.calls), "never ask for a non-positive page"
    assert len(client.calls) == 1, "an exhausted budget must stop the loop, not skip past zero"


# --- a page ceiling: the loop is bounded even against an endless cursor chain --


GUARD = 300  # a failing run must terminate rather than hang the suite


class EndlessEmptyClient:
    """Every page is empty and carries a fresh cursor, forever."""

    def __init__(self):
        self.calls = []

    def call(self, method, **params):
        self.calls.append((method, params))
        assert len(self.calls) <= GUARD, "collect did not stop"
        return {"ok": True, "messages": [],
                "response_metadata": {"next_cursor": f"c{len(self.calls)}"}}


class EndlessFilteredClient:
    """Every page is full of messages the filter rejects, with a fresh cursor."""

    def __init__(self):
        self.calls = []

    def call(self, method, **params):
        self.calls.append((method, params))
        assert len(self.calls) <= GUARD, "collect did not stop"
        items = [msg(len(self.calls) * 100 + k, "channel_join") for k in range(params["limit"])]
        return {"ok": True, "messages": items,
                "response_metadata": {"next_cursor": f"c{len(self.calls)}"}}


def test_the_ceiling_is_a_named_constant():
    from slackline import transcript

    assert transcript.MAX_PAGES == 100


def test_an_endless_chain_of_empty_pages_stops_at_the_ceiling():
    from slackline import transcript

    client = EndlessEmptyClient()
    result = run(client, max_messages=10)
    assert result.truncated_by == "max-pages"
    assert result.next_cursor
    assert result.pages == transcript.MAX_PAGES
    assert len(client.calls) == transcript.MAX_PAGES


def test_an_endless_chain_of_filtered_pages_stops_at_the_ceiling():
    from slackline import transcript

    client = EndlessFilteredClient()
    result = run(client, max_messages=10, limit=4)
    assert result.truncated_by == "max-pages"
    assert result.next_cursor
    assert result.filtered == transcript.MAX_PAGES * 4
    assert result.messages == []


def test_no_budget_is_still_page_bounded():
    from slackline import transcript

    client = EndlessEmptyClient()
    result = run(client, max_messages=None)
    assert result.truncated_by == "max-pages"
    assert len(client.calls) == transcript.MAX_PAGES


def test_a_normal_multi_page_read_is_far_below_the_ceiling(fake_client):
    client = fake_client({"conversations.history": THREE_PAGES})
    result = run(client)
    assert result.truncated_by is None
    assert result.pages == 3


# --- one page record for both commands (gate re-run item 8) ------------------


def test_page_record_has_the_shared_shape():
    from slackline.transcript import page_record

    record = page_record("C1", returned=3, filtered=1, next_cursor="abc",
                         truncated_by="max-messages")
    assert record == {
        "type": "page", "conversation_id": "C1", "returned": 3, "filtered": 1,
        "incomplete_threads": 0, "has_more": True, "next_cursor": "abc",
        "truncated_by": "max-messages",
    }


def test_page_record_has_more_is_the_cursor():
    from slackline.transcript import page_record

    assert page_record("C1", returned=0, filtered=0, next_cursor="", truncated_by=None)["has_more"] is False


def test_page_record_carries_thread_ts_only_when_given():
    from slackline.transcript import page_record

    with_thread = page_record("C1", returned=1, filtered=0, next_cursor="",
                              truncated_by=None, thread_ts="1.0")
    assert with_thread["thread_ts"] == "1.0"
    without = page_record("C1", returned=1, filtered=0, next_cursor="", truncated_by=None)
    assert "thread_ts" not in without


# --- review pass 2 of the gate re-run PR --------------------------------------


class OnePerPageClient:
    """One kept message per page and a cursor forever, with a call guard."""

    def __init__(self):
        self.calls = []

    def call(self, method, **params):
        self.calls.append((method, params))
        assert len(self.calls) <= GUARD, "collect did not stop"
        return {"ok": True, "messages": [msg(len(self.calls))],
                "response_metadata": {"next_cursor": f"c{len(self.calls)}"}}


def test_a_budget_spent_on_the_last_allowed_page_is_reported_as_the_budget():
    """The user-requested ceiling is the more specific cause; max-pages means
    nothing else stopped the read."""
    from slackline import transcript

    client = OnePerPageClient()
    result = run(client, max_messages=transcript.MAX_PAGES, limit=1)
    assert result.pages == transcript.MAX_PAGES
    assert result.truncated_by == "max-messages"


def test_the_first_page_reserve_lowers_only_the_first_request(fake_client):
    client = fake_client({"conversations.history": THREE_PAGES})
    run(client, max_messages=6, limit=4, first_page_reserve=1)
    assert limits_sent(client)[0] == 3


def test_the_first_page_reserve_does_not_apply_to_a_continuation(fake_client):
    """A cursor page carries no parent, so there is nothing to reserve for."""
    client = fake_client({"conversations.history": THREE_PAGES})
    run(client, max_messages=6, limit=4, first_page_reserve=1, cursor="p1")
    assert limits_sent(client)[0] == 4
