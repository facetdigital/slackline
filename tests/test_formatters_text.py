"""The text formatter writes to a terminal, and Slack content is untrusted (P8, P11)."""
from __future__ import annotations

from slackline.formatters import format_jsonl
from slackline.formatters.text import render_records


def message(text, name="joan"):
    return {"type": "message", "ts": "1756310000.000100",
            "time": "2026-08-29T10:00:00-07:00", "user": {"name": name}, "text": text}


def test_escape_sequences_do_not_reach_the_terminal():
    out = render_records([message("\x1b[31mred\x1b[0m and \x07bell")])
    assert "\x1b" not in out and "\x07" not in out
    assert "red" in out and "bell" in out


def test_newlines_and_tabs_survive():
    out = render_records([message("a\tb\nc")])
    assert "a\tb" in out
    assert "> c" in out


def test_c1_controls_are_stripped_too():
    out = render_records([message("x\x85y")])
    assert "xy" in out and "\x85" not in out


def test_names_are_cleaned_too():
    out = render_records([message("hello", name="jer\x1b[2Jemy")])
    assert "\x1b" not in out


def test_jsonl_is_untouched(capsys):
    """JSON encoding is the protection there; the bytes must survive escaped."""
    format_jsonl([message("\x1b[31m")])
    assert "\\u001b" in capsys.readouterr().out


def test_carriage_returns_are_stripped_everywhere():
    """A \\r lets a display name overwrite the row from column zero."""
    out = render_records([{"type": "user", "id": "U9", "handle": "bob",
                           "real_name": "Bob\rU999 @admin Root"}])
    assert "\r" not in out


def test_json_output_escapes_c1_and_del(capsys):
    """ensure_ascii=False leaves U+0080..U+009F and DEL as raw bytes, which
    xterm interprets; they must be escaped, which is still valid JSON."""
    import json

    format_jsonl([message("a\x9bb\x7fc")])
    out = capsys.readouterr().out
    assert "\x9b" not in out and "\x7f" not in out
    assert "\\u009b" in out
    assert json.loads(out)["text"] == "a\x9bb\x7fc"


def test_a_page_stopped_by_the_page_ceiling_says_so():
    out = render_records([{"type": "page", "returned": 12, "filtered": 0,
                           "incomplete_threads": 0, "has_more": True,
                           "next_cursor": "abc", "truncated_by": "max-pages"}])
    assert "max-pages ceiling" in out and "there is more" in out
    assert "abc" in out
    assert "--max-pages" not in out, "not a flag"


def test_json_and_raw_escape_c1_too(capsys):
    """Pins that format_json and format_raw share the escaping format_jsonl has;
    passes today by construction (_dumps is shared) and exists so it stays so."""
    from slackline.formatters import format_json, format_raw

    format_json([message("a\x9bb")])
    out = capsys.readouterr().out
    assert "\\u009b" in out and "\x9b" not in out
    format_raw([dict(message("a\x9bb"), _raw={"text": "a\x9bb"})])
    out = capsys.readouterr().out
    assert "\\u009b" in out and "\x9b" not in out
