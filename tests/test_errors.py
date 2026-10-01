"""Exit codes are a contract (D18), and tokens never survive into output (D4b)."""
from __future__ import annotations

import pytest

from slackline import errors


def test_every_error_carries_its_documented_exit_code():
    """The D18 table, asserted directly."""
    assert errors.SlackApiError("boom").exit_code == 1
    assert errors.ArgumentError("bad flag").exit_code == 2
    assert errors.AuthError("no token").exit_code == 3
    assert errors.AmbiguousSelector("two matches").exit_code == 4
    assert errors.NotFound("no such channel").exit_code == 5
    assert errors.ForbiddenMethod("chat.postMessage").exit_code == 7


def test_exit_code_six_is_unassigned():
    """D16: a successful but partial read exits 0. Nothing may claim 6.

    The seed proposal used 6 for partial results. Leaving it unassigned means
    that meaning cannot be reintroduced by accident.
    """
    used = {
        cls.exit_code
        for cls in vars(errors).values()
        if isinstance(cls, type)
        and issubclass(cls, errors.SlacklineError)
        and cls is not errors.SlacklineError
    }
    assert 6 not in used
    assert 0 not in used, "exit 0 means success and is never an error"


@pytest.mark.parametrize(
    "token",
    [
        "xoxp-1234567890-0987654321-abcdefghijklmnop",
        "xoxb-1111-2222-abcdef",
        "xapp-1-A01B2C3-4567-deadbeef",
        "xoxs-legacy-session-token",
    ],
)
def test_tokens_are_scrubbed_from_error_messages(token):
    """A token that reaches an exception must not reach stderr."""
    err = errors.SlackApiError(f"request failed using {token} against slack")
    rendered = str(err)
    assert token not in rendered
    assert "xox" in rendered, "the redaction should still be visible"
    assert "request failed using" in rendered, "surrounding context survives"


def test_scrub_handles_tokens_embedded_in_urls_and_json():
    dirty = (
        '{"error":"invalid_auth","sent":"Bearer xoxp-99-88-ffffffffff",'
        '"url":"https://slack.com/api/x?token=xoxp-99-88-ffffffffff"}'
    )
    clean = errors.scrub(dirty)
    assert "xoxp-99-88-ffffffffff" not in clean
    assert clean.count("xox***") == 2


def test_scrub_leaves_ordinary_text_alone():
    assert errors.scrub("no secrets here") == "no secrets here"
    assert errors.scrub("") == ""


def test_scrub_survives_non_string_input():
    """Exception args are not always strings."""
    assert errors.scrub(None) == "None"
    assert errors.scrub(42) == "42"


def test_ambiguous_selector_carries_candidates_for_the_caller():
    """D8: ambiguity is a hard failure that tells you how to disambiguate."""
    err = errors.AmbiguousSelector(
        "ambiguous user selector '@joan'",
        candidates=["U01ABC  Joan Archer (@joan)", "U02DEF  Joan Wong (@joan-w)"],
    )
    assert err.exit_code == 4
    assert len(err.candidates) == 2
    report = err.report()
    assert "candidates:" in report
    assert "Joan Wong" in report
