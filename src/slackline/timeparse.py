"""Time window parsing (D14).

``conversations.history`` bounds a read with ``oldest`` and ``latest``, both Unix
timestamps with fractional seconds. Everything here converts a human or agent
expression of time into that pair.

The rule running through this module: never guess. ``--since 48`` could mean
hours, days, or minutes, and picking one silently would produce a plausible but
wrong transcript. It is rejected instead.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Optional, Tuple

from .errors import ArgumentError

try:  # Python 3.9+
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

_DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*([smhdw])$", re.IGNORECASE)

_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def _zone(timezone: Optional[str]):
    if not timezone:
        return dt.datetime.now().astimezone().tzinfo
    if ZoneInfo is None:  # pragma: no cover
        raise ArgumentError("this Python has no zoneinfo; pass timestamps with offsets")
    try:
        return ZoneInfo(timezone)
    except Exception:
        raise ArgumentError(
            f"unknown timezone {timezone!r}. Expected an IANA name such as "
            f"'America/Los_Angeles'."
        ) from None


def parse_moment(value: str, now: dt.datetime, timezone: Optional[str]) -> float:
    """Parse one endpoint: a relative duration, a date, or an ISO timestamp."""
    value = (value or "").strip()
    if not value:
        raise ArgumentError("empty time value")

    match = _DURATION_RE.match(value)
    if match:
        amount, unit = float(match.group(1)), match.group(2).lower()
        return (now - dt.timedelta(seconds=amount * _UNIT_SECONDS[unit])).timestamp()

    if value.startswith("-"):
        raise ArgumentError(
            f"negative duration {value!r}. Durations are already in the past: "
            f"use '3d', not '-3d'."
        )

    # A bare integer is ambiguous by construction, so it is refused rather than
    # assigned a default unit that would silently produce the wrong window.
    if value.isdigit():
        raise ArgumentError(
            f"{value!r} has no unit. Write '{value}h' for hours, '{value}d' for "
            f"days, '{value}m' for minutes, or pass a full date."
        )

    zone = _zone(timezone)

    # A bare date means local midnight, which is what a person means by it.
    if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        naive = dt.datetime.strptime(value, "%Y-%m-%d")
        return naive.replace(tzinfo=zone).timestamp()

    normalized = value.replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(normalized)
    except ValueError:
        raise ArgumentError(
            f"could not parse time {value!r}. Expected a duration such as '48h', "
            f"a date such as '2026-08-25', or an ISO 8601 timestamp such as "
            f"'2026-08-25T09:00:00-07:00'."
        ) from None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.timestamp()


def window(
    since: Optional[str] = None,
    between: Optional[Tuple[str, str]] = None,
    after: Optional[str] = None,
    before: Optional[str] = None,
    now: Optional[dt.datetime] = None,
    timezone: Optional[str] = None,
) -> Tuple[Optional[float], Optional[float]]:
    """Resolve the requested window into ``(oldest, latest)`` epoch seconds."""
    now = now or dt.datetime.now(dt.timezone.utc)

    # Validate the zone up front, even for a relative window that does not need
    # it to compute bounds. The same zone renders every timestamp in the output,
    # so an unusable one must fail here rather than silently produce a transcript
    # in the wrong timezone.
    _zone(timezone)

    supplied = [name for name, value in
                (("--since", since), ("--between", between), ("--after", after)) if value]
    if len(supplied) > 1:
        raise ArgumentError(
            "conflicting time selectors: " + ", ".join(supplied) +
            ". Use exactly one of --since, --between, or --after/--before."
        )

    start: Optional[float] = None
    end: Optional[float] = None

    if since:
        start = parse_moment(since, now, timezone)
    elif between:
        start = parse_moment(between[0], now, timezone)
        end = parse_moment(between[1], now, timezone)
    else:
        if after:
            start = parse_moment(after, now, timezone)
        if before:
            end = parse_moment(before, now, timezone)

    if before and end is None:
        end = parse_moment(before, now, timezone)

    if start is not None and end is not None and start >= end:
        # Swapping them silently would hide the caller's bug and return data
        # they did not ask for.
        raise ArgumentError(
            "the start of the range is not before its end. Check the order of "
            "the values passed to --between, or --after and --before."
        )
    return start, end


def to_iso(slack_ts: str, timezone: Optional[str] = None) -> str:
    """Render a Slack ``ts`` as ISO 8601 with an offset.

    Never raises: an unparseable timestamp is returned as-is rather than taking
    down a transcript over one malformed field.
    """
    try:
        seconds = float(str(slack_ts).split(".")[0] + "." + str(slack_ts).split(".")[1])
    except (ValueError, IndexError):
        try:
            seconds = float(slack_ts)
        except (TypeError, ValueError):
            return str(slack_ts)
    try:
        zone = _zone(timezone)
    except ArgumentError:
        zone = dt.timezone.utc
    return dt.datetime.fromtimestamp(seconds, zone).isoformat(timespec="seconds")
