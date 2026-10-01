"""The directory cache (D7). Mandatory, not an optimization.

Resolving '@joan' needs the user directory, which means paginating users.list
over the workspace. Doing that per invocation turns a one-second tool into a
ten-second one, and agents call these tools in loops.
"""
from __future__ import annotations

import json
import os

import pytest

from slackline import cache as cache_mod


@pytest.fixture
def store(tmp_path):
    return cache_mod.Cache(root=tmp_path, team_id="T0ABC1234")


def test_write_then_read_round_trips(store):
    store.put("users", {"U1": {"name": "sam"}})
    assert store.get("users", max_age=3600) == {"U1": {"name": "sam"}}


def test_a_miss_returns_none(store):
    assert store.get("users", max_age=3600) is None


def test_an_expired_entry_is_a_miss(store, monkeypatch):
    store.put("users", {"U1": {}})
    now = cache_mod.time.time()
    monkeypatch.setattr(cache_mod.time, "time", lambda: now + 7200)
    assert store.get("users", max_age=3600) is None
    assert store.get("users", max_age=10800) is not None


def test_team_ids_partition_the_cache(tmp_path):
    """One workspace's directory must never answer another workspace's question."""
    a = cache_mod.Cache(root=tmp_path, team_id="T_AAA")
    b = cache_mod.Cache(root=tmp_path, team_id="T_BBB")
    a.put("users", {"U1": {"name": "from-a"}})
    assert b.get("users", max_age=3600) is None
    assert a.get("users", max_age=3600)["U1"]["name"] == "from-a"


def test_writes_are_atomic(store, monkeypatch):
    """A killed process must not leave a half-written cache that reads as valid."""
    store.put("users", {"U1": {"name": "good"}})

    def explode(*args, **kwargs):
        raise KeyboardInterrupt("killed mid-write")

    monkeypatch.setattr(cache_mod.os, "replace", explode)
    with pytest.raises(KeyboardInterrupt):
        store.put("users", {"U2": {"name": "partial"}})

    # The previous good value must still be intact and readable.
    assert store.get("users", max_age=3600) == {"U1": {"name": "good"}}
    leftovers = [p for p in store.root.iterdir() if p.suffix == ".tmp"]
    assert leftovers == [], "temp files must be cleaned up"


def test_corrupt_cache_is_treated_as_a_miss_not_a_crash(store):
    store.put("users", {"U1": {}})
    store.path_for("users").write_text("{ this is not json")
    assert store.get("users", max_age=3600) is None


def test_refresh_forces_a_miss_even_when_fresh(tmp_path):
    store = cache_mod.Cache(root=tmp_path, team_id="T1", refresh=True)
    store.put("users", {"U1": {}})
    assert store.get("users", max_age=3600) is None, "--refresh-cache must bypass reads"


def test_cache_refuses_message_content(store):
    """D7: the cache holds directory data. Message content never goes on disk."""
    with pytest.raises(ValueError):
        store.put("messages", {"C1": [{"text": "hello"}]})


def test_cache_directory_is_created_with_restrictive_permissions(tmp_path):
    store = cache_mod.Cache(root=tmp_path / "nested" / "slackline", team_id="T1")
    store.put("users", {})
    mode = os.stat(store.root).st_mode & 0o777
    assert mode == 0o700, "the cache names colleagues; it is not world-readable"


def test_default_root_honors_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("SLACKLINE_CACHE_DIR", str(tmp_path / "custom"))
    assert cache_mod.default_root() == tmp_path / "custom"
    monkeypatch.delenv("SLACKLINE_CACHE_DIR")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert cache_mod.default_root() == tmp_path / "xdg" / "slackline"


def test_stored_payload_records_when_it_was_written(store):
    store.put("users", {"U1": {}})
    raw = json.loads(store.path_for("users").read_text())
    assert "cached_at" in raw
    assert raw["team_id"] == "T0ABC1234"


# --- a path component is never built from an unchecked string (gate fix 12) --


@pytest.mark.parametrize("bad", ["../x", "", "T1/../../etc", "T1 T2"])
def test_a_team_id_that_is_not_a_slack_id_is_refused(tmp_path, bad):
    with pytest.raises(ValueError):
        cache_mod.Cache(root=tmp_path, team_id=bad)


@pytest.mark.parametrize("good", ["T0ABC1234", "T1", "unknown"])
def test_real_looking_team_ids_are_accepted(tmp_path, good):
    assert cache_mod.Cache(root=tmp_path, team_id=good).team_id == good


# --- the cache root follows the audit directory's rule (gate re-run item 4) --


def test_a_cache_root_slackline_did_not_create_is_left_alone(tmp_path):
    """SLACKLINE_CACHE_DIR may be $HOME or a shared checkout. A read-only tool
    does not change permissions it did not set; it says so instead (D7)."""
    root = tmp_path / "shared"
    root.mkdir()
    os.chmod(root, 0o755)
    warnings = []
    cache_mod.Cache(root=root, team_id="T1", warn=warnings.append).put("users", {})
    assert os.stat(root).st_mode & 0o777 == 0o755
    assert len(warnings) == 1 and "700" in warnings[0]


def test_no_warning_for_an_owner_only_root(tmp_path):
    root = tmp_path / "mine"
    root.mkdir()
    os.chmod(root, 0o700)
    warnings = []
    cache_mod.Cache(root=root, team_id="T1", warn=warnings.append).put("users", {})
    assert warnings == []


def test_the_warning_is_emitted_once_per_cache(tmp_path):
    root = tmp_path / "shared"
    root.mkdir()
    os.chmod(root, 0o755)
    warnings = []
    store = cache_mod.Cache(root=root, team_id="T1", warn=warnings.append)
    store.put("users", {})
    store.put("conversations", [])
    assert len(warnings) == 1


def test_warn_is_optional(tmp_path):
    root = tmp_path / "shared"
    root.mkdir()
    os.chmod(root, 0o755)
    cache_mod.Cache(root=root, team_id="T1").put("users", {})
    assert os.stat(root).st_mode & 0o777 == 0o755


def test_the_context_wires_the_cache_warning_to_stderr(tmp_path, monkeypatch, fake_client):
    """The hook is only useful if the CLI passes it; this pins the wiring."""
    from slackline import cli_read

    shared = tmp_path / "shared"
    shared.mkdir()
    os.chmod(shared, 0o755)
    monkeypatch.setenv("SLACKLINE_CACHE_DIR", str(shared))
    client = fake_client({
        "auth.test": {"ok": True, "user_id": "U1", "team_id": "T1"},
        "users.list": {"ok": True, "members": []},
    })
    ctx = cli_read.Context(client=client)
    warnings = []
    ctx.warn = warnings.append
    ctx.directory().users()
    assert any("not owner-only" in w for w in warnings)
    assert os.stat(shared).st_mode & 0o777 == 0o755


# --- the cache never writes through a planted symlink (review pass 2) --------


def test_a_symlinked_temp_file_is_not_followed(tmp_path):
    """The temp file is created exclusively; a link planted at its name is
    removed rather than written through."""
    victim = tmp_path / "victim"
    victim.write_text("keep")
    store = cache_mod.Cache(root=tmp_path, team_id="T1")
    (tmp_path / "users-T1.tmp").symlink_to(victim)
    store.put("users", {"U1": {}})
    assert victim.read_text() == "keep"
    assert store.get("users", max_age=3600) == {"U1": {}}


def test_a_symlinked_cache_file_is_refused_with_a_warning(tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("keep")
    warnings = []
    store = cache_mod.Cache(root=tmp_path, team_id="T1", warn=warnings.append)
    (tmp_path / "users-T1.json").symlink_to(victim)
    store.put("users", {"U1": {}})
    assert victim.read_text() == "keep"
    assert any("symlink" in w for w in warnings)


# --- security pass on the gate re-run PR ---------------------------------------


def test_a_directory_planted_at_the_temp_name_degrades_to_a_warning(tmp_path):
    """A cache that cannot be written is a slow run, not a failed one."""
    (tmp_path / "users-T1.tmp").mkdir()
    warnings = []
    store = cache_mod.Cache(root=tmp_path, team_id="T1", warn=warnings.append)
    store.put("users", {"U1": {}})
    assert any("cache" in w for w in warnings)


def test_a_symlinked_cache_file_is_a_miss_on_read(tmp_path):
    """A poisoned document behind a link in a shared root would decide which
    conversation a selector resolves to. A link is never read."""
    store = cache_mod.Cache(root=tmp_path, team_id="T1")
    store.put("users", {"U1": {"name": "real"}})
    real = store.path_for("users")
    poisoned = tmp_path / "poisoned.json"
    poisoned.write_text(real.read_text().replace("real", "fake"))
    real.unlink()
    real.symlink_to(poisoned)
    assert store.get("users", max_age=3600) is None


def test_the_looser_mode_warning_says_what_is_at_stake(tmp_path):
    root = tmp_path / "shared"
    root.mkdir()
    os.chmod(root, 0o755)
    warnings = []
    cache_mod.Cache(root=root, team_id="T1", warn=warnings.append).put("users", {})
    assert any("resolves" in w for w in warnings)
