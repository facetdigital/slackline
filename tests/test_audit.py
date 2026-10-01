"""The local audit log (D20).

The log exists to answer "what did the agent read, and when", after the fact,
without keeping a copy of what it read. Both halves matter. A log that recorded
message content would be a second, unmanaged copy of the workspace sitting in a
dotfile, which is a worse liability than the thing it was meant to make
auditable.
"""
from __future__ import annotations

import json
import os
import pathlib

import pytest

from slackline import audit


@pytest.fixture
def log_path(tmp_path):
    return tmp_path / "state" / "audit.jsonl"


def lines(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def entry(**overrides):
    base = {
        "command": "conversation",
        "argv": ["conversation", "--channel", "#general", "--since", "48h"],
        "conversations": ["C100"],
        "window": {"from": "2026-08-26T09:00:00-07:00", "to": None},
        "messages": 12,
        "duration_ms": 240,
        "exit_code": 0,
    }
    base.update(overrides)
    return base


# --- one line per invocation -----------------------------------------------


def test_an_invocation_appends_one_line(log_path):
    audit.record(path=log_path, **entry())
    assert len(lines(log_path)) == 1


def test_a_second_invocation_appends_rather_than_replaces(log_path):
    audit.record(path=log_path, **entry())
    audit.record(path=log_path, **entry(command="thread"))
    assert [row["command"] for row in lines(log_path)] == ["conversation", "thread"]


def test_each_line_is_independently_valid_json(log_path):
    audit.record(path=log_path, **entry())
    audit.record(path=log_path, **entry())
    for line in log_path.read_text().splitlines():
        json.loads(line)


def test_the_line_carries_the_fields_d20_requires(log_path):
    audit.record(path=log_path, **entry())
    row = lines(log_path)[0]
    for field in ("ts", "command", "argv", "conversations", "window",
                  "messages", "duration_ms", "exit_code"):
        assert field in row, f"D20 requires {field}"


def test_the_timestamp_is_recorded_even_though_the_caller_does_not_pass_one(log_path):
    audit.record(path=log_path, **entry())
    assert lines(log_path)[0]["ts"]


def test_a_failed_invocation_is_recorded_with_its_exit_code(log_path):
    """The reads that failed are the interesting ones in an audit."""
    audit.record(path=log_path, **entry(exit_code=5, messages=0))
    assert lines(log_path)[0]["exit_code"] == 5


def test_the_parent_directory_is_created_on_demand(log_path):
    assert not log_path.parent.exists()
    audit.record(path=log_path, **entry())
    assert log_path.exists()


# --- what must never appear ------------------------------------------------


def test_no_message_content_is_recorded(log_path):
    """The whole point. There is no field for it, and nothing smuggles it in."""
    audit.record(
        path=log_path,
        **entry(argv=["conversation", "--channel", "#general"]),
    )
    raw = log_path.read_text()
    assert "text" not in json.loads(raw)
    assert "I think we should postpone the migration" not in raw


def test_a_token_in_argv_is_scrubbed(log_path):
    """There is no --token flag, but an operator can still type one by mistake,
    and that mistake must not be persisted to a file (D4b)."""
    audit.record(
        path=log_path,
        **entry(argv=["whoami", "--token", "xoxp-9999-secret-value"]),
    )
    raw = log_path.read_text()
    assert "xoxp-9999-secret-value" not in raw
    assert "xox***" in raw


def test_a_token_anywhere_in_the_entry_is_scrubbed(log_path):
    """Scrubbing is applied to the serialized line, not to one blessed field."""
    audit.record(path=log_path, **entry(command="whoami xoxb-abc123"))
    assert "xoxb-abc123" not in log_path.read_text()


# --- disabling it ----------------------------------------------------------


def test_none_disables_the_log_entirely(tmp_path):
    assert audit.resolve_path({"SLACKLINE_AUDIT_LOG": "none"}) is None


def test_a_disabled_log_writes_nothing(log_path):
    audit.record(path=None, **entry())
    assert not log_path.exists()


def test_an_explicit_path_is_honored(tmp_path):
    target = tmp_path / "elsewhere.jsonl"
    assert audit.resolve_path({"SLACKLINE_AUDIT_LOG": str(target)}) == target


def test_the_default_lives_under_the_state_directory(tmp_path):
    resolved = audit.resolve_path({"XDG_STATE_HOME": str(tmp_path)})
    assert resolved == tmp_path / "slackline" / "audit.jsonl"


def test_the_default_falls_back_to_local_state_in_home(tmp_path):
    resolved = audit.resolve_path({"HOME": str(tmp_path)})
    assert resolved == tmp_path / ".local" / "state" / "slackline" / "audit.jsonl"


# --- failure to write is never a failure of the command --------------------


def test_an_unwritable_path_warns_and_does_not_raise(tmp_path):
    """A read that worked must not be reported as failed because a log file
    could not be opened. The command's job is reading Slack."""
    blocked = tmp_path / "blocked"
    blocked.write_text("I am a file, not a directory")
    warnings = []
    audit.record(path=blocked / "audit.jsonl", warn=warnings.append, **entry())
    assert warnings, "an unwritable audit log must say so"


def test_an_unwritable_path_is_silent_when_there_is_no_warn_hook(tmp_path):
    blocked = tmp_path / "blocked"
    blocked.write_text("I am a file, not a directory")
    audit.record(path=blocked / "audit.jsonl", **entry())


def test_a_directory_that_cannot_be_created_warns(tmp_path):
    unwritable_parent = tmp_path / "ro"
    unwritable_parent.mkdir()
    os.chmod(unwritable_parent, 0o500)
    warnings = []
    try:
        audit.record(
            path=unwritable_parent / "nested" / "audit.jsonl",
            warn=warnings.append,
            **entry(),
        )
        assert warnings
    finally:
        os.chmod(unwritable_parent, 0o700)


# --- wired into the CLI ----------------------------------------------------


def test_the_cli_records_an_invocation(tmp_path, monkeypatch, capsys):
    """End to end: a real run through main() leaves exactly one line."""
    from slackline import cli_read

    target = tmp_path / "audit.jsonl"
    monkeypatch.setenv("SLACKLINE_AUDIT_LOG", str(target))
    monkeypatch.delenv("SLACK_USER_TOKEN", raising=False)

    code = cli_read.main(["--version"])
    capsys.readouterr()

    assert code == 0
    rows = lines(target)
    assert len(rows) == 1
    assert rows[0]["exit_code"] == 0


def test_the_cli_records_a_failed_invocation(tmp_path, monkeypatch, capsys):
    from slackline import cli_read

    target = tmp_path / "audit.jsonl"
    monkeypatch.setenv("SLACKLINE_AUDIT_LOG", str(target))
    # No credential anywhere: an argument error is exit 2 regardless (D18).
    monkeypatch.chdir(tmp_path)

    code = cli_read.main(["conversation", "--nonsense"])
    capsys.readouterr()

    assert code == 2
    assert lines(target)[0]["exit_code"] == 2


def test_the_cli_honors_none(tmp_path, monkeypatch, capsys):
    from slackline import cli_read

    target = tmp_path / "audit.jsonl"
    monkeypatch.setenv("SLACKLINE_AUDIT_LOG", "none")
    cli_read.main(["--version"])
    capsys.readouterr()
    assert not target.exists()


def test_an_unwritable_audit_log_does_not_change_the_exit_code(
    tmp_path, monkeypatch, capsys
):
    """The invariant that matters most: auditing is observation, not control."""
    from slackline import cli_read

    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")
    monkeypatch.setenv("SLACKLINE_AUDIT_LOG", str(blocked / "audit.jsonl"))

    code = cli_read.main(["--version"])
    capsys.readouterr()
    assert code == 0


# --- owner-only, like the cache directory (gate fix 8) ---------------------


def test_the_log_file_is_created_owner_only(log_path):
    audit.record(path=log_path, **entry())
    assert os.stat(log_path).st_mode & 0o777 == 0o600


def test_the_state_directory_is_created_owner_only(log_path):
    audit.record(path=log_path, **entry())
    assert os.stat(log_path.parent).st_mode & 0o777 == 0o700


def test_an_existing_looser_file_is_tightened(log_path):
    log_path.parent.mkdir(parents=True)
    log_path.write_text("")
    os.chmod(log_path, 0o644)
    audit.record(path=log_path, **entry())
    assert os.stat(log_path).st_mode & 0o777 == 0o600


# --- review pass 1 of PR 3 ---------------------------------------------------


def test_a_directory_slackline_did_not_create_is_left_alone(tmp_path):
    """SLACKLINE_AUDIT_LOG may point anywhere, including the working directory
    or $HOME. Only a directory Slackline itself created is tightened; the log
    file is owner-only regardless."""
    shared = tmp_path / "shared"
    shared.mkdir()
    os.chmod(shared, 0o755)
    target = shared / "audit.jsonl"
    audit.record(path=target, **entry())
    assert os.stat(shared).st_mode & 0o777 == 0o755
    assert os.stat(target).st_mode & 0o777 == 0o600


# --- review pass 2 of PR 3 ---------------------------------------------------


def test_a_symlinked_log_is_refused_with_a_warning(tmp_path):
    """Policy changed by the gate re-run fixes: following a symlink let a
    link planted in a shared directory make Slackline append to any file the
    user can write. A symlink is now refused; the earlier test that pinned
    the follow-and-skip-chmod behavior is superseded by this one (D20)."""
    real = tmp_path / "real.jsonl"
    real.write_text("")
    os.chmod(real, 0o644)
    link = tmp_path / "audit.jsonl"
    link.symlink_to(real)
    warnings = []
    audit.record(path=link, warn=warnings.append, **entry())
    assert real.read_text() == ""
    assert os.stat(real).st_mode & 0o777 == 0o644
    assert any("symlink" in w for w in warnings)


def test_a_platform_without_o_nofollow_still_refuses_a_symlink(tmp_path, monkeypatch):
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
    real = tmp_path / "real.jsonl"
    real.write_text("")
    link = tmp_path / "audit.jsonl"
    link.symlink_to(real)
    warnings = []
    audit.record(path=link, warn=warnings.append, **entry())
    assert real.read_text() == ""
    assert warnings


def test_a_platform_without_fchmod_still_gets_its_audit_line(log_path, monkeypatch):
    """The permission nicety must never cost the record itself (D20)."""
    monkeypatch.delattr(os, "fchmod", raising=False)
    warnings = []
    audit.record(path=log_path, warn=warnings.append, **entry())
    assert len(lines(log_path)) == 1
    assert warnings == []


# --- security pass ------------------------------------------------------------


def test_a_directory_that_appeared_between_check_and_mkdir_is_not_chmodded(tmp_path, monkeypatch):
    """The check-then-create race: whether Slackline created the directory is
    decided by mkdir itself, never by a prior exists() that a planted symlink
    can invalidate."""
    victim = tmp_path / "victim"
    victim.mkdir()
    os.chmod(victim, 0o755)
    parent = tmp_path / "slackline"
    parent.symlink_to(victim)
    monkeypatch.setattr(pathlib.Path, "exists", lambda self: False)
    audit.record(path=parent / "audit.jsonl", **entry())
    assert os.stat(victim).st_mode & 0o777 == 0o755


def test_a_non_regular_file_is_never_re_permissioned(tmp_path):
    fifo = tmp_path / "audit.fifo"
    os.mkfifo(fifo)
    os.chmod(fifo, 0o622)
    reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    try:
        audit.record(path=fifo, **entry())
        assert os.stat(fifo).st_mode & 0o777 == 0o622
    finally:
        os.close(reader)


# --- security pass on the gate re-run PR ---------------------------------------


def test_a_fifo_at_the_log_path_is_refused_without_blocking(tmp_path):
    """A planted FIFO with no reader blocked the open forever. O_NONBLOCK turns
    that into an immediate refusal that lands in the warning."""
    import signal

    fifo = tmp_path / "audit.jsonl"
    os.mkfifo(fifo)
    warnings = []
    fired = []

    def bail(*_):
        fired.append(True)
        raise AssertionError("record() blocked on a FIFO")

    signal.signal(signal.SIGALRM, bail)
    signal.alarm(3)
    try:
        audit.record(path=fifo, warn=warnings.append, **entry())
    finally:
        signal.alarm(0)
    assert not fired, "the open blocked; the alarm is what returned"
    assert warnings


def test_a_hardlinked_log_is_not_written(tmp_path):
    """A hardlink at the log path to a file the user owns would receive the
    line and be re-permissioned. Only a file with a single link is written."""
    victim = tmp_path / "victim"
    victim.write_text("keep")
    link = tmp_path / "audit.jsonl"
    os.link(victim, link)
    warnings = []
    audit.record(path=link, warn=warnings.append, **entry())
    assert victim.read_text() == "keep"
    assert warnings
