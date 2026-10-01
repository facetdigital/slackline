"""Regressions for defects found in review of the core plumbing branch.

Each test here failed against the code as originally written. They live together
so the reasoning stays visible: these are the failure modes that survive a green
test suite, which is exactly the class of bug this project cares most about.
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from slackline import api, auth, cli_read, errors
from slackline.formatters import format_json
from slackline.formatters.text import render_records


def run_cli(*argv, env=None):
    import os

    environment = dict(os.environ)
    environment.pop("SLACK_USER_TOKEN", None)
    environment.update(env or {})
    return subprocess.run(
        [sys.executable, "-m", "slackline.cli_read", *argv],
        capture_output=True,
        text=True,
        env=environment,
        cwd="/tmp",  # away from the repo .env, so credentials are not picked up
    )


# --- argument handling -----------------------------------------------------


def test_timeout_without_a_value_is_an_argument_error():
    """Was: IndexError, reported as 'unexpected failure', exit 1."""
    result = run_cli("whoami", "--timeout")
    assert result.returncode == 2
    assert "--timeout requires a value" in result.stderr


def test_timeout_with_a_non_numeric_value_is_an_argument_error():
    """Was: ValueError escaping as exit 1."""
    result = run_cli("whoami", "--timeout", "abc")
    assert result.returncode == 2
    assert "number of seconds" in result.stderr


def test_timeout_accepts_the_equals_form_like_every_other_flag():
    """Was: silently swallowed, leaving the default 30s and raising nothing."""
    options, rest = cli_read._parse_global_flags(["--timeout=5"])
    assert options["timeout"] == 5.0
    assert rest == []


def test_a_nonpositive_timeout_is_rejected():
    with pytest.raises(errors.ArgumentError):
        cli_read._parse_global_flags(["--timeout", "0"])


def test_an_unimplemented_command_reports_cleanly():
    """Was: 'No module named slackline.commands.x', exit 1.

    COMMANDS advertised names that had no module yet, so dispatching to one
    crashed instead of saying it was unavailable.
    """
    with pytest.raises(errors.ArgumentError):
        cli_read._load_command("definitely_not_a_command")


def test_help_after_a_positional_argument_does_not_need_a_credential():
    """Was: dispatch skipped auth for '--help' anywhere, but the command only
    honored it at argv[0], so ctx.client stayed None and was dereferenced."""
    result = run_cli("whoami", "foo", "--help")
    assert result.returncode == 0
    assert "Usage:" in result.stdout
    assert "NoneType" not in result.stderr


def test_every_command_exposes_the_help_text_dispatch_relies_on():
    """Every name in COMMANDS must import and carry a non-empty HELP. A missing
    module is a failure here, not a skip: a skip would hide exactly the case
    this test exists to catch."""
    for name in cli_read.COMMANDS:
        module = cli_read._load_command(name)
        assert isinstance(getattr(module, "HELP", None), str) and module.HELP


# --- streams ---------------------------------------------------------------


def test_main_flushes_stdout_inside_the_guarded_region(monkeypatch):
    """Was: no flush before returning, so a closed downstream pipe surfaced at
    interpreter shutdown as exit 120 with 'Exception ignored in ...' on stderr,
    past every handler. The README's own example is a piped read.

    An earlier version of this test piped `--help` to `head` and passed against
    the bug, which made it worthless: help output is about 2KB and a pipe buffer
    is 64KB, so the write never actually failed. The defect is the missing flush,
    so that is what this asserts. The end-to-end version, which needs real
    volume to fill a pipe, lives in tests/live/.
    """
    flushed = []

    class Recording:
        def __init__(self, real):
            self._real = real

        def write(self, text):
            return self._real.write(text)

        def flush(self):
            flushed.append(True)
            return self._real.flush()

    monkeypatch.setattr(
        auth, "resolve_token", lambda **kwargs: auth.ResolvedToken("xoxp-stub", "env")
    )
    monkeypatch.setattr(
        api.SlackClient,
        "call",
        lambda self, method, **params: {
            "ok": True, "user": "u", "user_id": "U1", "team": "T", "team_id": "T1",
        },
    )
    monkeypatch.setattr(sys, "stdout", Recording(sys.stdout))
    assert cli_read.main(["whoami"]) == 0
    assert flushed, "main must flush stdout while BrokenPipeError is still catchable"


def test_broken_pipe_is_reported_as_success_not_as_a_crash(monkeypatch):
    """Companion to the test above, not a regression: the handler already
    existed. This pins the guarantee that it survives a stdout with no fileno,
    which is what a test harness or a wrapper object gives you."""
    class ClosedPipe:
        def write(self, text):
            raise BrokenPipeError(32, "Broken pipe")

        def flush(self):
            raise BrokenPipeError(32, "Broken pipe")

    monkeypatch.setattr(
        auth, "resolve_token", lambda **kwargs: auth.ResolvedToken("xoxp-stub", "env")
    )
    monkeypatch.setattr(
        api.SlackClient,
        "call",
        lambda self, method, **params: {
            "ok": True, "user": "u", "user_id": "U1", "team": "T", "team_id": "T1",
        },
    )
    monkeypatch.setattr(sys, "stdout", ClosedPipe())
    assert cli_read.main(["whoami"]) == 0


# --- HTTP ------------------------------------------------------------------


def test_a_200_response_that_is_not_json_is_a_clean_error():
    """Was: json.JSONDecodeError escaping the retry loop entirely.

    Realistic trigger: a captive portal or corporate proxy returning an HTML
    interstitial with status 200.
    """
    def transport(url, headers, timeout):
        return api._decode(b"<html>blocked by proxy</html>", 200)

    with pytest.raises(errors.SlackApiError) as caught:
        transport("", {}, 0)
    assert "not JSON" in str(caught.value)
    assert "proxy" in str(caught.value)


def test_network_failures_are_retried_like_server_errors():
    """Was: a DNS blip got zero retries while an HTTP 500 got five."""
    attempts = []

    def transport(url, headers, timeout):
        attempts.append(1)
        if len(attempts) < 3:
            raise api.TransportError("temporary failure in name resolution")
        return api.Response(200, {"ok": True}, api.Headers())

    client = api.SlackClient(token="xoxp-t", transport=transport)
    client.sleep = lambda _s: None
    assert client.call("auth.test")["ok"] is True
    assert len(attempts) == 3


def test_network_failure_eventually_gives_up_with_a_readable_error():
    def transport(url, headers, timeout):
        raise api.TransportError("connection reset by peer")

    client = api.SlackClient(token="xoxp-t", transport=transport)
    client.sleep = lambda _s: None
    with pytest.raises(errors.SlackApiError) as caught:
        client.call("auth.test")
    assert "could not reach Slack" in str(caught.value)


@pytest.mark.parametrize("casing", ["Retry-After", "retry-after", "RETRY-AFTER", "retry-After"])
def test_retry_after_is_honored_whatever_the_header_casing(casing):
    """Was: two spellings hand-coded, so any third dropped Slack's instruction.

    HTTP header names are case-insensitive by spec, and a proxy in the middle
    need not preserve Slack's casing.
    """
    slept = []
    responses = [
        api.Response(429, {"ok": False, "error": "ratelimited"}, api.Headers({casing: "9"})),
        api.Response(200, {"ok": True}, api.Headers()),
    ]
    client = api.SlackClient(token="xoxp-t", transport=lambda *a: responses.pop(0))
    client.sleep = slept.append
    client.call("auth.test")
    assert slept == [9.0]


def test_headers_are_case_insensitive_for_scope_reporting():
    headers = api.Headers({"X-OAuth-Scopes": "users:read"})
    assert headers.get("x-oauth-scopes") == "users:read"
    assert headers.get("X-OAUTH-SCOPES") == "users:read"


def test_boolean_parameters_are_encoded_the_way_slack_expects():
    """Was: urlencode rendered True as 'True', which Slack rejects."""
    assert api._encode_param(True) == "true"
    assert api._encode_param(False) == "false"
    assert api._encode_param(["a", "b"]) == "a,b"
    assert api._encode_param(200) == 200


def test_a_boolean_reaches_the_wire_lowercased():
    seen = {}

    def transport(url, headers, timeout):
        seen["url"] = url
        return api.Response(200, {"ok": True}, api.Headers())

    api.SlackClient(token="xoxp-t", transport=transport).call(
        "conversations.history", channel="C1", inclusive=True
    )
    assert "inclusive=true" in seen["url"]
    assert "inclusive=True" not in seen["url"]


# --- credentials -----------------------------------------------------------


def test_an_explicit_token_file_overrides_the_ambient_environment(monkeypatch, tmp_path):
    """Was: the env var won, so --token-file did the opposite of what it says."""
    monkeypatch.setenv("SLACK_USER_TOKEN", "xoxp-from-environment")
    token_file = tmp_path / "tok"
    token_file.write_text("xoxp-from-the-file")
    resolved = auth.resolve_token(token_file=token_file)
    assert resolved.token == "xoxp-from-the-file"
    assert resolved.source == "file"


def test_a_missing_token_file_is_an_error_even_when_the_environment_has_one(monkeypatch, tmp_path):
    """Was: unreachable. The env var satisfied the chain before the file was read."""
    monkeypatch.setenv("SLACK_USER_TOKEN", "xoxp-from-environment")
    with pytest.raises(errors.AuthError) as caught:
        auth.resolve_token(token_file=tmp_path / "nope")
    assert "does not exist" in str(caught.value)


# --- output ----------------------------------------------------------------


def test_json_output_shape_does_not_depend_on_how_many_results_there_are(capsys):
    """Was: one record emitted an object, two emitted an array.

    An agent parsing `channels --format json` would get an object when the user
    happened to be in exactly one channel, and an array otherwise.
    """
    format_json([{"type": "conversation", "id": "C1"}])
    single = json.loads(capsys.readouterr().out)
    format_json([{"type": "conversation", "id": "C1"}, {"type": "conversation", "id": "C2"}])
    multiple = json.loads(capsys.readouterr().out)
    assert isinstance(single, list) and isinstance(multiple, list)


def test_inherently_single_valued_records_stay_bare(capsys):
    """Companion to the test above, not a regression: confirms the fix for
    output-shape instability did not over-correct and flatten whoami too."""
    format_json([{"type": "identity", "user": {"name": "sam"}}])
    assert isinstance(json.loads(capsys.readouterr().out), dict)


def test_an_unrenderable_record_is_reported_not_dropped():
    """Was: silently skipped, so 'no renderer yet' looked like 'no results'.

    The same reasoning as D9, one layer up: never lose a record silently.
    """
    output = render_records([{"type": "brand_new_kind", "id": "X1"}])
    assert output.strip(), "an unknown record type must still produce output"
    assert "X1" in output


# --- redaction -------------------------------------------------------------


@pytest.mark.parametrize(
    "token",
    [
        "xoxd-abc%2Fdef%2Bghi%3D",   # percent-encoded cookie value
        "xoxc-abc/def+ghi=",          # base64-ish session token
        "xoxp-1111-2222-aaaa.bbbb",
    ],
)
def test_tokens_containing_url_and_base64_characters_are_fully_redacted(token):
    """Was: the character class stopped at '%', '/', '+' or '=', leaving the tail
    of the secret in the output. Partial redaction is worse than none, because
    it looks handled."""
    rendered = errors.scrub(f"failed with {token} today")
    assert token not in rendered
    for fragment in ("def", "ghi", "bbbb"):
        assert fragment not in rendered.replace("failed with", "")


# --- interrupt (gate nit) ---------------------------------------------------


def test_an_interrupt_exits_130_not_1(monkeypatch):
    """1 is the code for an API or unexpected failure (D18). A harness must be
    able to tell an interrupted run from a failed one; 130 is the convention."""
    monkeypatch.setattr(
        auth, "resolve_token", lambda **kwargs: auth.ResolvedToken("xoxp-stub", "env")
    )
    monkeypatch.setattr(api.SlackClient, "call", lambda self, method, **params: {"ok": True})

    class Interrupting:
        HELP = ""

        @staticmethod
        def parse(argv):
            return {}

        @staticmethod
        def run(ctx, argv, options=None):
            raise KeyboardInterrupt

    monkeypatch.setattr(cli_read, "_load_command", lambda name: Interrupting)
    assert cli_read.main(["whoami"]) == 130


def test_exit_code_130_is_documented():
    assert "130" in cli_read.EXIT_CODES


def test_stderr_is_cleaned_of_control_characters(monkeypatch, capsys):
    """stderr is a terminal too; ambiguity reports carry Slack-supplied labels."""
    monkeypatch.setattr(
        auth, "resolve_token", lambda **kwargs: auth.ResolvedToken("xoxp-stub", "env")
    )
    monkeypatch.setattr(api.SlackClient, "call", lambda self, method, **params: {"ok": True})

    class Ambiguous:
        HELP = ""

        @staticmethod
        def parse(argv):
            return {}

        @staticmethod
        def run(ctx, argv, options=None):
            ctx.warn("note \x1b[2J")
            raise errors.AmbiguousSelector("two\x1b[31m", candidates=["C1 \x9b bad"])

    monkeypatch.setattr(cli_read, "_load_command", lambda name: Ambiguous)
    assert cli_read.main(["whoami"]) == 4
    err = capsys.readouterr().err
    assert "\x1b" not in err and "\x9b" not in err
    assert "two" in err and "bad" in err
