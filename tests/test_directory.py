"""The user and conversation directory (D5, D6, D7).

The fixture reproduces the two shapes reconnaissance found that break naive
implementations: two users whose handles share a prefix, and two distinct group
DM IDs carrying the identical Slack-generated name.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from slackline import directory

FIXTURE = json.loads((pathlib.Path(__file__).parent / "fixtures" / "directory.json").read_text())


@pytest.fixture
def index(tmp_path, fake_client):
    client = fake_client(
        {
            "auth.test": {"ok": True, "user_id": "U001", "team_id": "T1", "user": "sam"},
            "users.list": {"ok": True, "members": FIXTURE["users"]},
            "users.conversations": {"ok": True, "channels": FIXTURE["conversations"]},
        }
    )
    from slackline.cache import Cache

    return directory.Directory(
        client=client, cache=Cache(root=tmp_path, team_id="T1"), self_user_id="U001"
    )


# --- users -----------------------------------------------------------------


def test_users_are_indexed_by_id_handle_and_real_name(index):
    assert index.user("U002")["handle"] == "joan"
    assert index.find_users("@joan")[0]["id"] == "U002"
    assert index.find_users("Joan Archer")[0]["id"] == "U002"


def test_a_bare_name_is_a_name_search_even_when_it_is_also_a_handle(index):
    """'joan' is U002's handle, and Joan Wong is also a Joan. Without the '@'
    the caller asked for a person named Joan, and there are two. Letting the
    handle short-circuit here picked one of them silently, which is the guess D8
    forbids; found in manual testing as '--with Kevin' with two Kevins."""
    assert {m["id"] for m in index.find_users("joan")} == {"U002", "U003"}


def test_an_at_selector_that_is_nobodys_handle_still_searches(index):
    """The '@' asks for an exact handle; when there is none it falls back to
    the same search a bare word gets, rather than failing outright."""
    assert {m["id"] for m in index.find_users("@joa")} == {"U002", "U003"}


def test_an_exact_handle_wins_over_a_prefix_match(index):
    """'@joan' must not be ambiguous just because '@joan-w' also exists."""
    matches = index.find_users("@joan")
    assert len(matches) == 1
    assert matches[0]["id"] == "U002"


def test_a_genuinely_ambiguous_name_returns_every_candidate(index):
    """A real name matching two people, where the name is nobody's handle.

    "Joan" cannot serve as this case: it is exactly U002's handle, and Slack
    handles are unique per workspace, so an exact handle match is unambiguous by
    construction. "Nelson" is a surname shared by two people and is nobody's
    handle, which is the situation the caller actually has to disambiguate.
    """
    matches = index.find_users("Nelson")
    assert {m["id"] for m in matches} == {"U004", "U007"}


def test_an_exact_handle_beats_a_name_that_matches_more_people(index):
    """Handles are unique, so they short-circuit. Display names are not, so they
    must never short-circuit, or a common first name would resolve to whoever
    happened to set it as their display name."""
    assert [m["id"] for m in index.find_users("@jamien")] == ["U007"]
    assert len(index.find_users("Jamie Nelson")) == 1


def test_deleted_users_are_indexed_so_old_transcripts_still_render(index):
    assert index.user("U005")["handle"] == "deactivated"
    assert index.user("U005")["deleted"] is True


def test_an_unknown_user_id_returns_none_rather_than_raising(index):
    assert index.user("U999") is None


def test_display_name_is_preferred_but_falls_back_to_real_name(index):
    assert index.user("U002")["display"] == "Joan"
    assert index.user("U004")["display"] == "Kim Nelson"


# --- conversations ---------------------------------------------------------


def test_conversations_are_classified_by_kind(index):
    kinds = {c["id"]: c["kind"] for c in index.conversations()}
    assert kinds["C100"] == "public"
    assert kinds["C101"] == "private"
    assert kinds["D200"] == "im"
    assert kinds["G300"] == "mpim"


def test_direct_messages_are_labelled_with_the_other_person(index):
    dm = index.conversation("D200")
    assert dm["label"] == "@joan"
    assert dm["members"] == ["U002"]


def test_group_dm_names_parse_into_member_handles(index):
    """215 of 215 real group DMs matched the mpdm-<handles>-N convention."""
    assert directory.parse_mpim_name("mpdm-sam--joan--kim-1") == [
        "sam", "joan", "kim"
    ]


def test_group_dm_parsing_survives_handles_containing_hyphens(index):
    """The separator is a double hyphen, so 'joan-w' stays intact."""
    assert directory.parse_mpim_name("mpdm-sam--joan-w--kim-1") == [
        "sam", "joan-w", "kim"
    ]


def test_a_non_mpim_name_parses_to_nothing(index):
    assert directory.parse_mpim_name("engineering") == []
    assert directory.parse_mpim_name("") == []


def test_colliding_group_dm_names_are_kept_as_separate_conversations(index):
    """The finding that reshaped the resolver.

    Two distinct conversation IDs in the real workspace share the name
    mpdm-sam--lee--bea-1. Collapsing them by name would silently
    read the wrong conversation.
    """
    matches = index.find_conversations_by_name("mpdm-sam--joan--kim-1")
    assert {c["id"] for c in matches} == {"G300", "G301"}


def test_channels_resolve_by_name_with_or_without_a_hash(index):
    assert index.find_conversations_by_name("#announcements")[0]["id"] == "C100"
    assert index.find_conversations_by_name("announcements")[0]["id"] == "C100"


# --- caching ---------------------------------------------------------------


def test_the_directory_is_built_once_and_then_cached(index):
    index.users()
    first = len(index.client.calls)
    index.users()
    index.users()
    assert len(index.client.calls) == first, "repeat access must not re-fetch"


def test_a_second_directory_reuses_the_cache_on_disk(tmp_path, fake_client):
    from slackline.cache import Cache

    responses = {
        "users.list": {"ok": True, "members": FIXTURE["users"]},
        "users.conversations": {"ok": True, "channels": FIXTURE["conversations"]},
    }
    first_client = fake_client(dict(responses))
    directory.Directory(first_client, Cache(root=tmp_path, team_id="T1"), "U001").users()

    second_client = fake_client({})  # any API call would raise
    reused = directory.Directory(second_client, Cache(root=tmp_path, team_id="T1"), "U001")
    assert reused.user("U002")["handle"] == "joan"
    assert second_client.calls == []


def test_the_directory_asks_slack_for_archived_rows_too(index):
    """Archived conversations are readable; filtering happens locally so the
    cache holds the whole inventory (D5, D7)."""
    index.conversations()
    call = next(p for m, p in index.client.calls if m == "users.conversations")
    assert call["exclude_archived"] in (False, "false")


def test_archived_namesakes_are_found_by_the_directory(index):
    """Name normalization lives in one place, so the warning about archived
    namesakes cannot drift from the resolution that set them aside."""
    assert [c["id"] for c in index.find_archived_namesakes("#engineering")] == ["C103"]
    assert index.find_archived_namesakes("engineering")[0]["id"] == "C103"
    assert index.find_archived_namesakes("#announcements") == []
    assert index.find_archived_namesakes("C103") == [], "an ID selector has no namesakes"
