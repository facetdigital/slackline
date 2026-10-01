"""channels and users: discovery without being told IDs first (PRD 9.2)."""
from __future__ import annotations

import json
import pathlib

import pytest

from slackline import cli_read
from slackline.cache import Cache
from slackline.commands import channels as channels_cmd
from slackline.commands import users as users_cmd
from slackline.directory import Directory

FIXTURE = json.loads((pathlib.Path(__file__).parent / "fixtures" / "directory.json").read_text())


@pytest.fixture
def ctx(tmp_path, fake_client):
    client = fake_client(
        {
            "users.list": {"ok": True, "members": FIXTURE["users"]},
            "users.conversations": {"ok": True, "channels": FIXTURE["conversations"]},
        }
    )
    context = cli_read.Context(client=client, fmt="jsonl")
    context._directory = Directory(client, Cache(root=tmp_path, team_id="T1"), "U001")
    return context


def records(ctx, module, argv, capsys):
    code = module.run(ctx, list(argv))
    ctx.flush()
    out = capsys.readouterr().out
    return code, [json.loads(line) for line in out.strip().splitlines() if line]


# --- channels --------------------------------------------------------------


def test_channels_lists_every_conversation_kind(ctx, capsys):
    code, rows = records(ctx, channels_cmd, [], capsys)
    assert code == 0
    assert {r["kind"] for r in rows} == {"public", "private", "im", "mpim"}


def test_channels_filters_by_kind(ctx, capsys):
    _, rows = records(ctx, channels_cmd, ["--kind", "im"], capsys)
    assert rows and all(r["kind"] == "im" for r in rows)


def test_channels_accepts_several_kinds(ctx, capsys):
    _, rows = records(ctx, channels_cmd, ["--kind", "public,private"], capsys)
    assert {r["kind"] for r in rows} == {"public", "private"}


def test_an_unknown_kind_is_an_argument_error(ctx, capsys):
    from slackline import errors

    with pytest.raises(errors.ArgumentError):
        channels_cmd.run(ctx, ["--kind", "telepathy"])


def test_channels_matches_on_a_substring(ctx, capsys):
    _, rows = records(ctx, channels_cmd, ["--match", "announce"], capsys)
    assert [r["id"] for r in rows] == ["C100"]


def test_direct_messages_show_who_they_are_with(ctx, capsys):
    _, rows = records(ctx, channels_cmd, ["--kind", "im"], capsys)
    labels = {r["id"]: r["label"] for r in rows}
    assert labels["D200"] == "@joan"


def test_colliding_group_dms_both_appear_with_distinct_ids(ctx, capsys):
    """The user must be able to see that the collision exists (D6)."""
    _, rows = records(ctx, channels_cmd, ["--kind", "mpim"], capsys)
    colliding = [r for r in rows if r["name"] == "mpdm-sam--joan--kim-1"]
    assert len(colliding) == 2
    assert {r["id"] for r in colliding} == {"G300", "G301"}


# --- users -----------------------------------------------------------------


def test_users_lists_humans_by_default(ctx, capsys):
    _, rows = records(ctx, users_cmd, [], capsys)
    ids = {r["id"] for r in rows}
    assert "U002" in ids
    assert "U006" not in ids, "bots are excluded by default"
    assert "U005" not in ids, "deactivated users are excluded by default"


def test_users_can_include_bots_and_deactivated_accounts(ctx, capsys):
    _, rows = records(ctx, users_cmd, ["--include-bots", "--include-deleted"], capsys)
    ids = {r["id"] for r in rows}
    assert "U006" in ids
    assert "U005" in ids


def test_users_matches_on_handle_and_real_name(ctx, capsys):
    _, rows = records(ctx, users_cmd, ["--match", "kim"], capsys)
    assert [r["id"] for r in rows] == ["U004"]
    _, rows = records(ctx, users_cmd, ["--match", "Nelson"], capsys)
    assert {r["id"] for r in rows} == {"U004", "U007"}


def test_text_output_is_a_readable_table(ctx, capsys):
    ctx.fmt = "text"
    users_cmd.run(ctx, ["--match", "kim"])
    ctx.flush()
    out = capsys.readouterr().out
    assert "U004" in out and "kim" in out and "Kim Nelson" in out


# --- archived conversations (gate fix 4) -----------------------------------


def test_archived_conversations_are_hidden_by_default(ctx, capsys):
    _, rows = records(ctx, channels_cmd, [], capsys)
    ids = {r["id"] for r in rows}
    assert "C102" not in ids and "C103" not in ids


def test_include_archived_shows_them(ctx, capsys):
    """The flag was documented and did nothing: the directory never fetched
    archived rows, so there was nothing for it to show."""
    _, rows = records(ctx, channels_cmd, ["--include-archived"], capsys)
    archived = {r["id"]: r["archived"] for r in rows if r["id"] in ("C102", "C103")}
    assert archived == {"C102": True, "C103": True}
