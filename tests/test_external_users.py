"""People from another workspace, in a channel shared with it (Slack Connect).

The directory is built from ``users.list``, which returns only our own
workspace (D7). Someone on the other side of a shared channel is not in it, so
before this was handled their messages rendered with the speaker ``unknown``
and mentions of them as a raw ``@U...`` ID. Shared client channels are where a
transcript most needs to say who said what.

Resolution order, when an ID is not in our directory:

1. the ``user_profile`` Slack attaches to a message from an external user;
2. ``users.info`` on the ID, cached like the rest of the directory;
3. the ID itself, because an unresolvable user never breaks a transcript.

The message shapes follow Slack's documented ``user_profile`` and ``user_team``
fields. Every name, ID, and team in them is made up.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from slackline import cli_read
from slackline.cache import Cache
from slackline.commands import conversation as conversation_cmd
from slackline.directory import Directory
from slackline.errors import NotFound
from slackline.resolve import Resolver

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
DIR_FIXTURE = json.loads((FIXTURES / "directory.json").read_text())
MSG = json.loads((FIXTURES / "messages.json").read_text())

#: What users.info knows about the external people in the fixtures. U802 is
#: mentioned but never speaks, so users.info is the only way to name them.
USERS_INFO = {
    "U801": {"id": "U801", "team_id": "T2", "name": "jane.doe", "is_bot": False,
             "deleted": False,
             "profile": {"real_name": "Jane Doe", "display_name": "jane", "team": "T2"}},
    "U802": {"id": "U802", "team_id": "T2", "name": "omar.h", "is_bot": False,
             "deleted": False,
             "profile": {"real_name": "Omar Haddad", "display_name": "", "team": "T2"}},
    # Someone in our own workspace who joined after the directory was cached.
    "U020": {"id": "U020", "team_id": "T1", "name": "newhire", "is_bot": False,
             "deleted": False,
             "profile": {"real_name": "Nora Hale", "display_name": "Nora", "team": "T1"}},
}


def users_info(**params):
    user = USERS_INFO.get(params.get("user"))
    if user is None:
        return NotFound("users.info: user_not_found. The conversation does not "
                        "exist, or this user cannot see it.")
    return {"ok": True, "user": user}


def history(*messages):
    return {"ok": True, "messages": list(messages), "has_more": False,
            "response_metadata": {"next_cursor": ""}}


@pytest.fixture
def ctx_for(tmp_path, fake_client):
    def build(history_page, fmt="jsonl"):
        client = fake_client({
            "users.list": {"ok": True, "members": DIR_FIXTURE["users"]},
            "users.conversations": {"ok": True, "channels": DIR_FIXTURE["conversations"]},
            "users.info": users_info,
            "conversations.history": history_page,
            "conversations.replies": {"ok": True, "messages": []},
        })
        ctx = cli_read.Context(client=client, fmt=fmt)
        cache = Cache(root=tmp_path, team_id="T1")
        ctx._directory = Directory(client, cache, "U001")
        ctx._resolver = Resolver(client, ctx._directory, "U001", cache=cache)
        ctx._identity = {"user_id": "U001", "team_id": "T1"}
        return ctx

    return build


def read(ctx, capsys):
    code = conversation_cmd.run(ctx, ["--channel", "#announcements"])
    ctx.flush()
    return code, capsys.readouterr().out


def users_info_calls(ctx):
    return [params.get("user") for method, params in ctx.client.calls
            if method == "users.info"]


# --- the author ------------------------------------------------------------


def test_an_external_author_is_named_and_marked_external_in_text(ctx_for, capsys):
    ctx = ctx_for(history(MSG["external_user"]), fmt="text")
    code, out = read(ctx, capsys)
    assert code == 0
    assert "@Jane Doe (ext):" in out
    assert "unknown:" not in out


def test_an_external_author_is_named_from_the_message_without_a_lookup(ctx_for, capsys):
    """The message already says who sent it; asking Slack again is waste."""
    ctx = ctx_for(history(MSG["external_user"]))
    _, out = read(ctx, capsys)
    message = next(json.loads(line) for line in out.splitlines()
                   if json.loads(line)["type"] == "message")
    assert message["user"]["id"] == "U801"
    assert message["user"]["display"] == "Jane Doe"
    assert message["user"]["external"] is True
    assert message["user"]["team"] == "T2"
    assert users_info_calls(ctx) == []


def test_our_own_people_are_not_marked_external(ctx_for, capsys):
    ctx = ctx_for(history(MSG["plain_text"]))
    _, out = read(ctx, capsys)
    message = next(json.loads(line) for line in out.splitlines()
                   if json.loads(line)["type"] == "message")
    assert message["user"]["name"] == "joan"
    assert message["user"]["external"] is False


# --- mentions --------------------------------------------------------------


def test_a_mention_of_an_external_user_already_seen_uses_their_profile(ctx_for, capsys):
    ctx = ctx_for(history(MSG["external_user"], MSG["mentions_external"]), fmt="text")
    _, out = read(ctx, capsys)
    assert "> @Jane Doe (ext) sounds good." in out
    assert "U801" not in users_info_calls(ctx)


def test_a_mention_of_an_unseen_external_user_is_looked_up_once(ctx_for, capsys):
    mentions_twice = dict(MSG["mentions_external"], ts="1756310050.000100")
    ctx = ctx_for(history(MSG["mentions_external"], mentions_twice), fmt="text")
    _, out = read(ctx, capsys)
    assert "@Omar Haddad (ext)" in out
    assert "@U802" not in out
    assert users_info_calls(ctx).count("U802") == 1


def test_a_user_nobody_can_resolve_still_renders_as_the_id(ctx_for, capsys):
    """users.info failing is not a failed read. The ID is what is left."""
    unresolvable = dict(MSG["mentions_external"], text="ask <@U899> about it")
    ctx = ctx_for(history(unresolvable, dict(unresolvable, ts="1756310050.000100")),
                  fmt="text")
    code, out = read(ctx, capsys)
    assert code == 0
    assert "> ask @U899 about it" in out
    assert users_info_calls(ctx).count("U899") == 1


def test_a_lookup_that_finds_one_of_our_own_people_is_not_marked_external(ctx_for, capsys):
    """Someone who joined after the directory was cached is a colleague."""
    newcomer = dict(MSG["mentions_external"], text="welcome <@U020>")
    ctx = ctx_for(history(newcomer), fmt="text")
    _, out = read(ctx, capsys)
    assert "> welcome @newhire" in out
    assert "(ext)" not in out


# --- caching ---------------------------------------------------------------


def test_external_users_are_cached_like_the_rest_of_the_directory(tmp_path, fake_client):
    """A second invocation names an external user without asking Slack again."""
    responses = {
        "users.list": {"ok": True, "members": DIR_FIXTURE["users"]},
        "users.info": users_info,
    }
    first = fake_client(responses)
    Directory(first, Cache(root=tmp_path, team_id="T1"), "U001").handle_for("U802")

    second = fake_client(responses)
    names = Directory(second, Cache(root=tmp_path, team_id="T1"), "U001")
    assert names.handle_for("U802") == "@Omar Haddad (ext)"
    assert [m for m, _ in second.calls if m == "users.info"] == []
