"""Selector resolution (D6, D8, PRD P10, PRD 9.3).

The hardest correctness problem in the MVP. Group DM names are not unique: two
distinct conversation IDs in the live workspace carry the name
'mpdm-sam--lee--bea-1'. Resolving by name alone would silently
read the wrong conversation, and the caller would have no way to tell.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from slackline import errors, resolve
from slackline.cache import Cache
from slackline.directory import Directory

FIXTURE = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "directory.json").read_text()
)

# G300 and G301 share a name. Their member sets differ, which is the only
# reliable way to tell them apart.
MEMBERS = {
    "G300": ["U001", "U002", "U004"],
    "G301": ["U001", "U002", "U007"],
    "G302": ["U001", "U003", "U004"],
    # An archived group DM with the same members as the live G300, which is
    # what Slack produces when the same people start a new one.
    "G399": ["U001", "U002", "U004"],
    "C100": ["U001", "U002"],
}


@pytest.fixture
def resolver(tmp_path, fake_client):
    # conversations.info returns no 'members' key for group DMs. Verified against
    # the live workspace, which is why the resolver uses conversations.members.
    # The fake mirrors that: an info response here deliberately omits members.
    def conversations_members(channel=None, **_):
        return {"ok": True, "members": MEMBERS.get(channel, []),
                "response_metadata": {"next_cursor": ""}}

    client = fake_client(
        {
            "users.list": {"ok": True, "members": FIXTURE["users"]},
            "users.conversations": {"ok": True, "channels": FIXTURE["conversations"]},
            "conversations.info": lambda channel=None, **_: {
                "ok": True, "channel": {"id": channel}
            },
            "conversations.members": conversations_members,
        }
    )
    directory = Directory(client, Cache(root=tmp_path, team_id="T1"), "U001")
    return resolve.Resolver(client=client, directory=directory, self_user_id="U001")


# --- channels --------------------------------------------------------------


def test_a_channel_name_resolves(resolver):
    assert resolver.resolve(channels=["#announcements"])[0]["id"] == "C100"


def test_a_channel_id_resolves(resolver):
    assert resolver.resolve(channels=["C100"])[0]["id"] == "C100"


def test_an_unknown_channel_is_not_found(resolver):
    with pytest.raises(errors.NotFound) as caught:
        resolver.resolve(channels=["#nope"])
    assert caught.value.exit_code == 5


# --- direct messages -------------------------------------------------------


def test_a_single_person_resolves_to_the_one_to_one_dm(resolver):
    matches = resolver.resolve(with_users=["@joan"])
    assert len(matches) == 1
    assert matches[0]["id"] == "D200"
    assert matches[0]["kind"] == "im"


def test_an_ambiguous_person_is_a_hard_failure_listing_candidates(resolver):
    with pytest.raises(errors.AmbiguousSelector) as caught:
        resolver.resolve(with_users=["Nelson"])
    assert caught.value.exit_code == 4
    report = caught.value.report()
    assert "U004" in report and "U007" in report


def test_a_bare_name_two_people_share_is_ambiguous_even_if_one_holds_it_as_a_handle(resolver):
    with pytest.raises(errors.AmbiguousSelector) as caught:
        resolver.resolve(with_users=["joan"])
    report = caught.value.report()
    assert "U002" in report and "U003" in report
    # The way out is named, so an agent can retry without guessing.
    assert "@handle" in report


def test_an_unknown_person_is_not_found(resolver):
    with pytest.raises(errors.NotFound):
        resolver.resolve(with_users=["@nobody"])


# --- group DMs: the collision case ----------------------------------------


def test_a_group_dm_is_disambiguated_by_its_member_set(resolver):
    """Two conversations share a name. Only membership separates them."""
    matches = resolver.resolve(with_users=["@joan", "@kim"])
    assert len(matches) == 1
    assert matches[0]["id"] == "G300", "must pick by members, not by name order"


def test_a_stale_name_does_not_hide_the_right_conversation(resolver):
    """G301 is NAMED for joan and kim but its members are joan and jamien.

    This is not contrived. Group DM names record the handles people had when the
    conversation was created, and Slack renames deactivated accounts. In the live
    workspace, a group DM named '...--lee--...' now contains
    'deactivateduser000001'. Filtering by current handles alone would exclude the
    exact conversation the caller asked for, so the name is a fast path and the
    membership scan is the authority.
    """
    matches = resolver.resolve(with_users=["@joan", "@jamien"])
    assert [m["id"] for m in matches] == ["G301"]


def test_the_fallback_scan_announces_itself_on_stderr(resolver):
    warnings = []
    resolver.warn = warnings.append
    resolver.resolve(with_users=["@joan", "@jamien"])
    assert any("scanning membership" in w for w in warnings), (
        "extra work must be visible, not silent"
    )


def test_membership_is_verified_with_a_read_only_method(resolver):
    """conversations.info returns no members key for group DMs, verified live.

    conversations.members is the only method that actually answers the question,
    and it is read-only.
    """
    resolver.resolve(with_users=["@joan", "@kim"])
    methods = {method for method, _ in resolver.client.calls}
    assert "conversations.members" in methods
    from slackline import api

    assert "conversations.members" in api.READ_ONLY_METHODS


def test_conversations_open_is_never_called(resolver):
    """It would resolve group DMs neatly, but it can create one (D3, D6)."""
    resolver.resolve(with_users=["@joan", "@kim"])
    methods = {method for method, _ in resolver.client.calls}
    assert "conversations.open" not in methods


def test_a_member_set_with_no_conversation_is_not_found(resolver):
    with pytest.raises(errors.NotFound):
        resolver.resolve(with_users=["@kim", "@jamien"])


def test_a_genuine_tie_is_ambiguous_and_prints_both_ids(resolver, monkeypatch):
    """When two conversations really do share a member set, refuse to choose."""
    monkeypatch.setitem(MEMBERS, "G301", ["U001", "U002", "U004"])
    try:
        with pytest.raises(errors.AmbiguousSelector) as caught:
            resolver.resolve(with_users=["@joan", "@kim"])
        report = caught.value.report()
        assert "G300" in report and "G301" in report
    finally:
        MEMBERS["G301"] = ["U001", "U002", "U007"]


def test_the_narrowing_pass_keeps_membership_calls_cheap(resolver):
    """Name parsing narrows 215 group DMs to a couple before verification."""
    resolver.resolve(with_users=["@joan", "@kim"])
    probes = [c for c in resolver.client.calls if c[0] == "conversations.members"]
    assert len(probes) <= 3, "must not probe every group DM in the workspace"


# --- selector hygiene ------------------------------------------------------


def test_mixing_selector_kinds_is_an_argument_error(resolver):
    with pytest.raises(errors.ArgumentError):
        resolver.resolve(channels=["#announcements"], with_users=["@joan"])


def test_no_selector_at_all_is_an_argument_error(resolver):
    with pytest.raises(errors.ArgumentError):
        resolver.resolve()


def test_a_conversation_id_bypasses_resolution_entirely(resolver):
    """An explicit ID is already unambiguous; do not second-guess it."""
    matches = resolver.resolve(conversations=["G301"])
    assert [m["id"] for m in matches] == ["G301"]


def test_several_channels_resolve_to_several_conversations(resolver):
    matches = resolver.resolve(channels=["#announcements", "#engineering"])
    assert {m["id"] for m in matches} == {"C100", "C101"}


def test_self_is_not_required_in_the_with_list(resolver):
    """'--with joan --with kim' means the DM containing me and them."""
    assert resolver.resolve(with_users=["@joan", "@kim"])[0]["id"] == "G300"


def test_naming_yourself_explicitly_is_harmless(resolver):
    assert resolver.resolve(with_users=["@sam", "@joan", "@kim"])[0]["id"] == "G300"


def test_membership_probes_are_cached_across_invocations(tmp_path, fake_client):
    """The fallback scan costs ~9s against the live workspace. Pay it once."""
    from slackline import resolve as resolve_mod

    def members(channel=None, **_):
        return {"ok": True, "members": MEMBERS.get(channel, []),
                "response_metadata": {"next_cursor": ""}}

    responses = {
        "users.list": {"ok": True, "members": FIXTURE["users"]},
        "users.conversations": {"ok": True, "channels": FIXTURE["conversations"]},
        "conversations.members": members,
    }
    cache = Cache(root=tmp_path, team_id="T1")

    first_client = fake_client(dict(responses))
    first = resolve_mod.Resolver(
        first_client, Directory(first_client, cache, "U001"), "U001", cache=cache
    )
    first.resolve(with_users=["@joan", "@kim"])
    probes = len([c for c in first_client.calls if c[0] == "conversations.members"])
    assert probes >= 1

    second_client = fake_client(dict(responses))
    second = resolve_mod.Resolver(
        second_client, Directory(second_client, cache, "U001"), "U001", cache=cache
    )
    second.resolve(with_users=["@joan", "@kim"])
    assert [c for c in second_client.calls if c[0] == "conversations.members"] == []


# --- archived conversations (gate fix 4) -----------------------------------


def test_a_live_conversation_wins_over_an_archived_one_with_the_same_name(resolver):
    """C103 is an archived channel that shares the name of the live C101. A
    name must resolve to what people mean by it, not to a tie with a corpse."""
    assert [m["id"] for m in resolver.resolve(channels=["#engineering"])] == ["C101"]


def test_an_archived_conversation_is_reachable_by_id(resolver):
    assert resolver.resolve(conversations=["C103"])[0]["id"] == "C103"


def test_a_name_that_only_matches_archived_rows_resolves_to_them(resolver):
    assert [m["id"] for m in resolver.resolve(channels=["#old-project"])] == ["C102"]


# --- verified members in the result (gate fix 7) ---------------------------


def test_a_group_dm_found_by_the_fallback_scan_reports_its_verified_members(resolver):
    """G301 is named for joan and kim but contains joan and jamien. The
    member list a caller sees must be the one that was verified, not the one
    parsed from a stale name (D6)."""
    match = resolver.resolve(with_users=["@joan", "@jamien"])[0]
    assert match["members"] == ["U001", "U002", "U007"]


def test_a_name_matched_group_dm_also_reports_verified_members(resolver):
    match = resolver.resolve(with_users=["@joan", "@kim"])[0]
    assert match["members"] == ["U001", "U002", "U004"]


def test_a_tie_report_prints_each_candidates_members(resolver, monkeypatch):
    """D6 step 5: a tie prints both IDs and their member lists."""
    monkeypatch.setitem(MEMBERS, "G301", ["U001", "U002", "U004"])
    try:
        with pytest.raises(errors.AmbiguousSelector) as caught:
            resolver.resolve(with_users=["@joan", "@kim"])
        candidates = [line for line in caught.value.report().splitlines()
                      if line.strip().startswith("G30")]
        assert len(candidates) == 2
        assert all("members:" in line and "@kim" in line and "@joan" in line
                   for line in candidates)
    finally:
        MEMBERS["G301"] = ["U001", "U002", "U007"]


# --- archived group DMs (review pass 1 of PR 2) ----------------------------


def test_a_live_group_dm_wins_over_an_archived_one_with_the_same_members(resolver):
    """G399 is archived and has exactly G300's members. Slack creates a new
    conversation rather than reviving an old one, so this pair is the ordinary
    shape, and it must not read as a tie."""
    assert [m["id"] for m in resolver.resolve(with_users=["@joan", "@kim"])] == ["G300"]


def test_the_fallback_scan_probes_live_rows_and_skips_archived_ones_once_matched(resolver):
    """The fallback scan's probe budget goes to live conversations. Every live
    row is still probed after a match, because two live matches are a tie;
    archived rows are not, because they would lose the preference anyway."""
    resolver.resolve(with_users=["@joan", "@jamien"])
    probed = [p["channel"] for m, p in resolver.client.calls if m == "conversations.members"]
    assert "G301" in probed and "G302" in probed
    assert "G399" not in probed


def test_a_genuine_tie_between_live_group_dms_is_still_ambiguous(resolver, monkeypatch):
    monkeypatch.setitem(MEMBERS, "G301", ["U001", "U002", "U004"])
    try:
        with pytest.raises(errors.AmbiguousSelector) as caught:
            resolver.resolve(with_users=["@joan", "@kim"])
        report = caught.value.report()
        assert "G300" in report and "G301" in report
        assert "G399" not in report, "an archived row does not join a live tie"
    finally:
        MEMBERS["G301"] = ["U001", "U002", "U007"]


def test_an_archived_namesake_is_announced_when_a_name_resolves_to_the_live_one(resolver):
    """D8's carve-out for archived namesakes is not silent: the caller hears
    that an archived conversation of the same name exists and how to read it."""
    warnings = []
    resolver.warn = warnings.append
    assert [m["id"] for m in resolver.resolve(channels=["#engineering"])] == ["C101"]
    assert any("archived" in w and "C103" in w for w in warnings)


def test_no_warning_when_a_name_has_no_archived_namesake(resolver):
    warnings = []
    resolver.warn = warnings.append
    resolver.resolve(channels=["#announcements"])
    assert warnings == []


# --- review pass 2 of PR 2 ---------------------------------------------------


def test_a_discarded_archived_group_dm_is_announced(resolver):
    """The member-set path is as loud as the name path about what it set aside."""
    warnings = []
    resolver.warn = warnings.append
    resolver.resolve(with_users=["@joan", "@kim"])
    assert any("archived" in w and "G399" in w for w in warnings)


def test_archived_name_matches_are_not_probed_when_a_live_one_confirms(resolver):
    """Live candidates are probed first; an archived name match costs a call
    only if nothing live confirmed."""
    resolver.resolve(with_users=["@joan", "@kim"])
    probed = [p["channel"] for m, p in resolver.client.calls if m == "conversations.members"]
    assert "G300" in probed
    assert "G399" not in probed


def test_an_archived_only_member_set_still_resolves(resolver, monkeypatch):
    """When no live group DM has the members, the archived one is the answer."""
    monkeypatch.setitem(MEMBERS, "G300", ["U001", "U002"])
    assert [m["id"] for m in resolver.resolve(with_users=["@joan", "@kim"])] == ["G399"]


def test_a_not_found_probe_on_one_candidate_does_not_fail_the_resolution(tmp_path, fake_client):
    """An archived or stale candidate can answer channel_not_found. That is
    "not this one", not a reason to abandon a resolution a live row satisfies."""
    def members(channel=None, **_):
        if channel == "G301":
            raise errors.NotFound("conversations.members: channel_not_found")
        return {"ok": True, "members": MEMBERS.get(channel, []),
                "response_metadata": {"next_cursor": ""}}

    client = fake_client({
        "users.list": {"ok": True, "members": FIXTURE["users"]},
        "users.conversations": {"ok": True, "channels": FIXTURE["conversations"]},
        "conversations.members": members,
    })
    warnings = []
    directory = Directory(client, Cache(root=tmp_path, team_id="T1"), "U001")
    resolver = resolve.Resolver(client=client, directory=directory, self_user_id="U001",
                                warn=warnings.append)
    assert [m["id"] for m in resolver.resolve(with_users=["@joan", "@kim"])] == ["G300"]
    assert any("G301" in w for w in warnings)


def test_the_set_aside_warning_on_the_scan_path_is_worded_by_members(resolver, monkeypatch):
    """On the fallback membership scan the name did not match by definition,
    so the warning must not say it did."""
    monkeypatch.setitem(MEMBERS, "G399", ["U001", "U002", "U007"])  # archived twin of G301
    warnings = []
    resolver.warn = warnings.append
    assert [m["id"] for m in resolver.resolve(with_users=["@joan", "@jamien"])] == ["G301"]
    set_aside = [w for w in warnings if "G399" in w]
    assert set_aside, "the archived twin was set aside and must be announced"
    assert all("these members" in w and "matching name" not in w for w in set_aside)


def test_the_set_aside_warning_on_the_name_path_still_says_name(resolver):
    warnings = []
    resolver.warn = warnings.append
    resolver.resolve(with_users=["@joan", "@kim"])
    assert any("G399" in w and "matching name" in w for w in warnings)
