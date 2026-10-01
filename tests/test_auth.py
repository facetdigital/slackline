"""Credential resolution (D4a). Headless first, never blocking on a human."""
from __future__ import annotations

import pytest

from slackline import auth, errors

TOKEN_A = "xoxp-aaaa-1111-token"
TOKEN_B = "xoxp-bbbb-2222-token"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ("SLACK_USER_TOKEN", "SLACKLINE_OP_REF", "SLACKLINE_OP_ACCOUNT"):
        monkeypatch.delenv(var, raising=False)
    # These tests walk the chain; the Keychain step is stubbed above the
    # subprocess layer so no `security` helper is ever spawned (invariant 10).
    monkeypatch.setattr(auth, "_read_from_keychain", lambda: None)


def test_environment_variable_is_the_first_source(monkeypatch, tmp_path):
    """P16: an agent with only an env var must succeed, and succeed first."""
    monkeypatch.setenv("SLACK_USER_TOKEN", TOKEN_A)
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"SLACK_USER_TOKEN={TOKEN_B}\n")
    resolved = auth.resolve_token(dotenv_path=dotenv)
    assert resolved.token == TOKEN_A
    assert resolved.source == "env"


def test_token_file_beats_dotenv(tmp_path):
    token_file = tmp_path / "tok"
    token_file.write_text(TOKEN_A + "\n")
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"SLACK_USER_TOKEN={TOKEN_B}\n")
    resolved = auth.resolve_token(token_file=token_file, dotenv_path=dotenv)
    assert resolved.token == TOKEN_A
    assert resolved.source == "file"


def test_dotenv_is_the_last_resort(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"# a comment\nSLACK_USER_TOKEN={TOKEN_B}\nOTHER=x\n")
    resolved = auth.resolve_token(dotenv_path=dotenv)
    assert resolved.token == TOKEN_B
    assert resolved.source == "dotenv"


def test_dotenv_tolerates_quotes_and_whitespace(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text(f'  SLACK_USER_TOKEN = "{TOKEN_B}"  \n')
    assert auth.resolve_token(dotenv_path=dotenv).token == TOKEN_B


def test_no_credential_anywhere_is_an_auth_error(tmp_path):
    with pytest.raises(errors.AuthError) as caught:
        auth.resolve_token(dotenv_path=tmp_path / "missing.env")
    assert caught.value.exit_code == 3
    assert "SLACK_USER_TOKEN" in str(caught.value), "the error must say how to fix it"


def test_forcing_a_source_disables_fallthrough(monkeypatch, tmp_path):
    """A headless job must fail loudly, not silently reach for an interactive source."""
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"SLACK_USER_TOKEN={TOKEN_B}\n")
    with pytest.raises(errors.AuthError) as caught:
        auth.resolve_token(source="env", dotenv_path=dotenv)
    assert caught.value.exit_code == 3
    assert "env" in str(caught.value)


def test_forcing_an_unknown_source_is_an_argument_error():
    with pytest.raises(errors.ArgumentError):
        auth.resolve_token(source="telepathy")


def test_op_source_is_never_reached_without_explicit_configuration(monkeypatch, tmp_path):
    """1Password prompts for TouchID. It must never be entered by accident."""
    called = []
    monkeypatch.setattr(auth, "_read_from_op", lambda *a, **k: called.append(1))
    with pytest.raises(errors.AuthError):
        auth.resolve_token(dotenv_path=tmp_path / "missing.env")
    assert called == [], "op must not run unless SLACKLINE_OP_REF is set"


def test_op_source_is_used_when_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("SLACKLINE_OP_REF", "op://Employee/Slack/credential")
    monkeypatch.setattr(auth, "_read_from_op", lambda ref, account: TOKEN_A)
    resolved = auth.resolve_token(dotenv_path=tmp_path / "missing.env")
    assert resolved.token == TOKEN_A
    assert resolved.source == "op"


def test_a_source_that_raises_is_skipped_not_fatal(monkeypatch, tmp_path):
    """Keychain being locked or absent must not break an env-var invocation."""
    monkeypatch.setattr(auth, "_read_from_keychain", lambda: (_ for _ in ()).throw(OSError("no keychain")))
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"SLACK_USER_TOKEN={TOKEN_B}\n")
    assert auth.resolve_token(dotenv_path=dotenv).token == TOKEN_B


def test_there_is_no_token_flag():
    """P18: a token in argv is visible in ps and recorded in shell history."""
    import inspect
    from slackline import cli_read

    source = inspect.getsource(cli_read) + inspect.getsource(auth)
    assert '"--token"' not in source
    assert "'--token'" not in source


def test_a_malformed_token_is_rejected_with_guidance():
    with pytest.raises(errors.AuthError) as caught:
        auth.validate_token("not-a-slack-token")
    assert "xoxp-" in str(caught.value)


def test_a_bot_token_is_rejected_because_slackline_acts_as_the_user():
    with pytest.raises(errors.AuthError) as caught:
        auth.validate_token("xoxb-1111-2222-bot")
    assert "user token" in str(caught.value).lower()


def test_validate_accepts_a_user_token():
    assert auth.validate_token("xoxp-1-2-3") == "xoxp-1-2-3"


def test_auth_error_for_a_bad_token_does_not_echo_the_token():
    with pytest.raises(errors.AuthError) as caught:
        auth.validate_token("xoxb-secret-value-here")
    assert "secret-value-here" not in caught.value.report()


def test_the_no_credential_message_states_the_real_order(tmp_path):
    """The message is the fifth place a reader learns the order from (D4a)."""
    with pytest.raises(errors.AuthError) as caught:
        auth.resolve_token(dotenv_path=tmp_path / "missing.env")
    text = str(caught.value)
    assert "consulted first" in text
    assert "in order: --token-file" not in text
