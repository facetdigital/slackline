"""Time windows to Slack epoch bounds (D14, PRD P3)."""
from __future__ import annotations

import datetime as dt

import pytest

from slackline import errors, timeparse

TZ = "America/Los_Angeles"


def at(*args):
    return dt.datetime(*args, tzinfo=dt.timezone(dt.timedelta(hours=-7)))


NOW = at(2026, 8, 27, 15, 0, 0)


# --- relative durations ----------------------------------------------------


@pytest.mark.parametrize(
    "value,hours",
    [("1h", 1), ("48h", 48), ("7d", 168), ("30m", 0.5), ("2w", 336), ("90s", 0.025)],
)
def test_relative_durations(value, hours):
    start, end = timeparse.window(since=value, now=NOW, timezone=TZ)
    assert end is None
    assert abs((NOW.timestamp() - start) - hours * 3600) < 1


def test_a_bare_number_is_rejected_rather_than_guessed():
    """'--since 48' could mean hours, days, or minutes. Never assume."""
    with pytest.raises(errors.ArgumentError) as caught:
        timeparse.window(since="48", now=NOW, timezone=TZ)
    assert "unit" in str(caught.value).lower()


def test_an_unknown_unit_is_an_argument_error():
    with pytest.raises(errors.ArgumentError):
        timeparse.window(since="5 fortnights", now=NOW, timezone=TZ)


def test_a_negative_duration_is_rejected():
    with pytest.raises(errors.ArgumentError):
        timeparse.window(since="-3d", now=NOW, timezone=TZ)


# --- absolute timestamps ---------------------------------------------------


def test_a_bare_date_means_local_midnight():
    start, _ = timeparse.window(since="2026-08-25", now=NOW, timezone=TZ)
    rendered = dt.datetime.fromtimestamp(start, dt.timezone(dt.timedelta(hours=-7)))
    assert (rendered.hour, rendered.minute) == (0, 0)
    assert rendered.date() == dt.date(2026, 8, 25)


def test_iso_with_an_explicit_offset_is_honored_exactly():
    start, _ = timeparse.window(since="2026-08-25T09:00:00-07:00", now=NOW, timezone=TZ)
    assert start == at(2026, 8, 25, 9, 0, 0).timestamp()


def test_a_naive_timestamp_uses_the_supplied_timezone():
    start, _ = timeparse.window(since="2026-08-25T09:00:00", now=NOW, timezone=TZ)
    assert start == at(2026, 8, 25, 9, 0, 0).timestamp()


def test_an_unparseable_timestamp_is_an_argument_error():
    with pytest.raises(errors.ArgumentError):
        timeparse.window(since="last tuesday", now=NOW, timezone=TZ)


# --- ranges ----------------------------------------------------------------


def test_between_produces_both_bounds():
    start, end = timeparse.window(
        between=("2026-08-25", "2026-08-27"), now=NOW, timezone=TZ
    )
    assert start < end


def test_after_and_before_compose():
    start, end = timeparse.window(
        after="2026-08-25T09:00:00-07:00", before="2026-08-26T17:00:00-07:00",
        now=NOW, timezone=TZ,
    )
    assert start == at(2026, 8, 25, 9, 0, 0).timestamp()
    assert end == at(2026, 8, 26, 17, 0, 0).timestamp()


def test_before_alone_leaves_the_start_open():
    start, end = timeparse.window(before="2026-08-26", now=NOW, timezone=TZ)
    assert start is None
    assert end is not None


def test_an_inverted_range_is_an_argument_error():
    """Silently swapping them would hide a caller's bug."""
    with pytest.raises(errors.ArgumentError) as caught:
        timeparse.window(between=("2026-08-27", "2026-08-25"), now=NOW, timezone=TZ)
    assert "before" in str(caught.value).lower() or "after" in str(caught.value).lower()


def test_combining_since_and_between_is_an_argument_error():
    with pytest.raises(errors.ArgumentError):
        timeparse.window(since="48h", between=("2026-08-25", "2026-08-27"),
                         now=NOW, timezone=TZ)


def test_no_window_at_all_is_allowed_and_unbounded():
    assert timeparse.window(now=NOW, timezone=TZ) == (None, None)


# --- formatting ------------------------------------------------------------


def test_slack_timestamps_render_as_iso_with_offset():
    rendered = timeparse.to_iso("1756310000.000100", TZ)
    assert rendered.startswith("2025-") or rendered.startswith("2026-")
    assert "+" in rendered or "-" in rendered[10:]


def test_an_invalid_slack_timestamp_does_not_raise():
    assert isinstance(timeparse.to_iso("not-a-ts", TZ), str)


def test_an_unknown_timezone_is_an_argument_error():
    with pytest.raises(errors.ArgumentError):
        timeparse.window(since="1h", now=NOW, timezone="Mars/Olympus_Mons")
