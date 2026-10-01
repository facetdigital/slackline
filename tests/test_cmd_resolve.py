"""``resolve``: what would be read, without reading it (PRD 9.3, D6)."""
from __future__ import annotations

import json
import pathlib

import pytest

from slackline import cli_read
from slackline.cache import Cache
from slackline.commands import resolve as resolve_cmd
from slackline.directory import Directory
from slackline.resolve import Resolver

DIR_FIXTURE = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "directory.json").read_text()
)

MEMBERS = {
    "G300": ["U001", "U002", "U004"],
    "G301": ["U001", "U002", "U007"],
    "G302": ["U001", "U003", "U004"],
    "G399": ["U001", "U002", "U004"],  # archived twin of G300
}


@pytest.fixture
def ctx(tmp_path, fake_client):
    def members(channel=None, **_):
        return {"ok": True, "members": MEMBERS.get(channel, []),
                "response_metadata": {"next_cursor": ""}}

    client = fake_client(
        {
            "users.list": {"ok": True, "members": DIR_FIXTURE["users"]},
            "users.conversations": {"ok": True, "channels": DIR_FIXTURE["conversations"]},
            "conversations.members": members,
        }
    )
    context = cli_read.Context(client=client, fmt="jsonl")
    cache = Cache(root=tmp_path, team_id="T1")
    context._directory = Directory(client, cache, "U001")
    context._resolver = Resolver(client, context._directory, "U001", cache=cache)
    context._identity = {"user_id": "U001", "team_id": "T1"}
    return context


def resolution(ctx, argv, capsys):
    code = resolve_cmd.run(ctx, list(argv))
    ctx.flush()
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    assert code == 0
    return next(r for r in rows if r["type"] == "resolution")


def test_a_group_dm_reports_its_verified_members_as_handles(ctx, capsys):
    """The stale-name case: the name says kim, the verified members say jamien."""
    result = resolution(ctx, ["--with", "@joan", "--with", "@jamien"], capsys)
    assert len(result["matches"]) == 1
    assert result["matches"][0]["id"] == "G301"
    assert result["matches"][0]["members"] == ["@jamien", "@joan", "@sam"]


def test_member_handles_are_sorted(ctx, capsys):
    result = resolution(ctx, ["--with", "@joan", "--with", "@kim"], capsys)
    members = result["matches"][0]["members"]
    assert members == sorted(members)


def test_a_channel_resolution_carries_no_member_list(ctx, capsys):
    result = resolution(ctx, ["--channel", "#announcements"], capsys)
    assert result["matches"][0]["members"] == []


def test_the_selector_is_echoed_back(ctx, capsys):
    result = resolution(ctx, ["--with", "@joan", "--with", "@kim"], capsys)
    assert result["selector"] == {"with_users": ["@joan", "@kim"]}


def test_the_resolution_says_whether_a_conversation_is_archived(ctx, capsys):
    """An agent that never saw an archived channel in `channels` must not read
    one without being told what it is."""
    result = resolution(ctx, ["--channel", "#old-project"], capsys)
    assert result["matches"][0]["id"] == "C102"
    assert result["matches"][0]["archived"] is True
    result = resolution(ctx, ["--channel", "#announcements"], capsys)
    assert result["matches"][0]["archived"] is False
