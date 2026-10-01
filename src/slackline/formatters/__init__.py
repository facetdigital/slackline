"""Output formatters.

Every command produces a list of typed records. A formatter turns records into
bytes on stdout. Nothing else is ever written to stdout (D19).

The format is chosen explicitly and never inferred from the environment. Slackline
does not call ``isatty()`` (D12, PRD P15): a harness that allocates a pty must not
silently receive a different contract than one that does not.
"""
from __future__ import annotations

import json
import re
import sys
from typing import Any, Callable, Dict, List

FORMATS = ("text", "jsonl", "json", "raw")

# json.dumps with ensure_ascii=False escapes only U+0000..U+001F. DEL and the
# C1 range go out as raw UTF-8, which a terminal such as xterm interprets when
# JSON is piped to the screen. Escaping them is still valid JSON and loses
# nothing on parse; the text formatter strips the same range instead.
_RAW_CONTROL_RE = re.compile(r"[\x7f-\x9f]")


def _dumps(payload: Any, **kwargs: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, **kwargs)
    return _RAW_CONTROL_RE.sub(lambda m: "\\u%04x" % ord(m.group()), encoded)


def _write(text: str) -> None:
    sys.stdout.write(text)


def format_jsonl(records: List[Dict[str, Any]]) -> None:
    for record in records:
        _write(_dumps(record, separators=(",", ":")) + "\n")


#: Record types that are single-valued by nature. Only these are emitted bare.
#:
#: Unwrapping on "len(records) == 1" instead would make the output shape depend
#: on the data: `channels --format json` would return an object for a user in
#: one channel and an array for everyone else. Every consumer would then have to
#: handle both, and would discover the second shape in production rather than in
#: testing.
SINGLE_VALUED = frozenset({"identity", "resolution"})


def format_json(records: List[Dict[str, Any]]) -> None:
    payload: Any = records
    if len(records) == 1 and records[0].get("type") in SINGLE_VALUED:
        payload = records[0]
    _write(_dumps(payload, indent=2) + "\n")


def format_raw(records: List[Dict[str, Any]]) -> None:
    """Debugging escape hatch: whatever Slack actually said (PRD P9)."""
    for record in records:
        _write(_dumps(record.get("_raw", record), indent=2) + "\n")


def format_text(records: List[Dict[str, Any]]) -> None:
    from .text import render_records

    _write(render_records(records))


def get_formatter(name: str) -> Callable[[List[Dict[str, Any]]], None]:
    from ..errors import ArgumentError

    try:
        return {
            "text": format_text,
            "jsonl": format_jsonl,
            "json": format_json,
            "raw": format_raw,
        }[name]
    except KeyError:
        raise ArgumentError(
            f"unknown --format '{name}'. Expected one of: " + ", ".join(FORMATS)
        ) from None
