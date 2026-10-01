"""whoami: prove which identity and workspace the tool is operating as (PRD 9.1)."""
from __future__ import annotations

import json

from slackline import cli_read
from slackline.commands import whoami


AUTH_TEST = {
    "ok": True,
    "user": "sam",
    "user_id": "U0ABC1234",
    "team": "Example Co",
    "team_id": "T0ABC1234",
    "url": "https://example.slack.com/",
}
SCOPE_HEADERS = {"X-OAuth-Scopes": "identify,channels:history,users:read"}


def run(capsys, client, argv=()):
    ctx = cli_read.Context(client=client, fmt="text")
    code = whoami.run(ctx, list(argv))
    ctx.flush()
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_reports_identity_and_workspace(capsys, fake_client):
    client = fake_client({"auth.test": AUTH_TEST}, headers=SCOPE_HEADERS)
    code, out, err = run(capsys, client)
    assert code == 0
    assert "sam" in out
    assert "U0ABC1234" in out
    assert "Example Co" in out
    assert "T0ABC1234" in out


def test_reports_granted_scopes_from_the_response_header(capsys, fake_client):
    """The scope list is how a caller finds out why something is not permitted."""
    client = fake_client({"auth.test": AUTH_TEST}, headers=SCOPE_HEADERS)
    _, out, _ = run(capsys, client)
    assert "channels:history" in out
    assert "users:read" in out


def test_flags_the_absence_of_write_scopes_as_the_expected_state(capsys, fake_client):
    """A read-only tool holding no write scope is the design, so say so."""
    client = fake_client({"auth.test": AUTH_TEST}, headers=SCOPE_HEADERS)
    _, out, _ = run(capsys, client)
    assert "read-only" in out.lower()


def test_json_output_is_valid_and_machine_shaped(capsys, fake_client):
    client = fake_client({"auth.test": AUTH_TEST}, headers=SCOPE_HEADERS)
    ctx = cli_read.Context(client=client, fmt="json")
    whoami.run(ctx, [])
    ctx.flush()
    payload = json.loads(capsys.readouterr().out)
    assert payload["user"]["name"] == "sam"
    assert payload["team"]["id"] == "T0ABC1234"
    assert "channels:history" in payload["scopes"]


def test_nothing_but_the_payload_reaches_stdout(capsys, fake_client):
    """D19: stdout carries requested data only."""
    client = fake_client({"auth.test": AUTH_TEST}, headers=SCOPE_HEADERS)
    ctx = cli_read.Context(client=client, fmt="jsonl")
    whoami.run(ctx, [])
    ctx.flush()
    out = capsys.readouterr().out
    for line in out.strip().splitlines():
        json.loads(line)


def test_missing_scope_header_does_not_break_the_command(capsys, fake_client):
    client = fake_client({"auth.test": AUTH_TEST}, headers={})
    code, out, _ = run(capsys, client)
    assert code == 0
    assert "sam" in out
