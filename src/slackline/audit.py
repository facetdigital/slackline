"""One JSON line per invocation, recording what was read but never what it said (D20).

The log answers "what did this agent read, and when", after the fact, without
keeping a copy of the reading. Both halves are load-bearing. A log that included
message content would be a second, unmanaged copy of the workspace living in a
dotfile, which is a worse liability than the one it was meant to make auditable.

Writing the log is observation, never control: every failure here degrades to a
warning on stderr. A read that succeeded must not be reported as failed because
a file could not be opened.
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import stat
from typing import Any, Callable, Dict, List, Optional

from .errors import scrub

ENV_VAR = "SLACKLINE_AUDIT_LOG"
DISABLED = "none"

#: The fields D20 requires, in the order they are most useful to read.
FIELDS = ("ts", "command", "argv", "conversations", "window", "messages",
          "duration_ms", "exit_code")


def resolve_path(env: Optional[Dict[str, str]] = None) -> Optional[pathlib.Path]:
    """Where the log goes, or None when it is switched off.

    Explicit setting wins, then XDG_STATE_HOME, then ~/.local/state. The state
    directory rather than the cache directory: this is a record that should
    survive a cache clear, and it is not reconstructible from anything.
    """
    env = os.environ if env is None else env

    setting = env.get(ENV_VAR)
    if setting:
        if setting.strip().lower() == DISABLED:
            return None
        return pathlib.Path(setting).expanduser()

    state_home = env.get("XDG_STATE_HOME")
    if state_home:
        return pathlib.Path(state_home).expanduser() / "slackline" / "audit.jsonl"

    home = env.get("HOME") or os.path.expanduser("~")
    return pathlib.Path(home) / ".local" / "state" / "slackline" / "audit.jsonl"


def record(
    path: Optional[pathlib.Path],
    command: Optional[str] = None,
    argv: Optional[List[str]] = None,
    conversations: Optional[List[str]] = None,
    window: Optional[Dict[str, Any]] = None,
    messages: int = 0,
    duration_ms: int = 0,
    exit_code: int = 0,
    warn: Optional[Callable[[str], None]] = None,
) -> None:
    """Append one line. Never raises."""
    if path is None:
        return

    entry = {
        "ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "command": command,
        "argv": list(argv or []),
        "conversations": list(conversations or []),
        "window": window,
        "messages": messages,
        "duration_ms": duration_ms,
        "exit_code": exit_code,
    }

    try:
        # Scrub the serialized line rather than a chosen field. Redacting only
        # argv would hold as long as no future field ever carries a token,
        # which is a promise about code that has not been written yet.
        line = scrub(json.dumps(entry, ensure_ascii=False, separators=(",", ":")))
        # Owner-only, for the same reason the cache directory is: the log names
        # the conversations read and carries argv. It has no business being
        # readable by other accounts on a shared machine (D20).
        #
        # Only a directory Slackline created is tightened. The path is user
        # configurable, and its parent may be the working directory or $HOME;
        # a read-only tool does not change permissions it did not set. Whether
        # this process created it is decided by mkdir itself, never by a prior
        # existence check that a symlink planted in between could invalidate.
        path.parent.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.mkdir(path.parent, 0o700)
        except FileExistsError:
            pass
        else:
            try:
                os.chmod(path.parent, 0o700)
            except OSError:
                pass
        # Never through a symlink. A link planted at the log path in a shared
        # directory would make a read-only tool append to any file the user
        # can write. Checked before the open, and enforced again by O_NOFOLLOW
        # where the platform has it, so a link swapped in between fails the
        # open with ELOOP and lands in the warning below (D20).
        if path.is_symlink():
            if warn is not None:
                warn(f"refusing to write the audit log through a symlink at {path}")
            return
        # Append mode, one write, newline included: concurrent invocations
        # interleave whole lines rather than corrupting each other. The mode
        # is fixed on the open descriptor before anything is written, so a
        # pre-existing looser file is never appended to at its old mode.
        # O_NONBLOCK: a FIFO planted at the path with no reader would otherwise
        # block the open forever; with it the open fails at once and lands in
        # the warning. Harmless on a regular file.
        flags = (os.O_WRONLY | os.O_APPEND | os.O_CREAT
                 | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        descriptor = os.open(path, flags, 0o600)
        try:
            # Only a regular file with a single link that this account owns is
            # written: a FIFO, a device node, a hardlink to some other file, or
            # a file another account pre-created is not a file Slackline made,
            # and a read-only tool does not write into those. The line is then
            # dropped with a warning rather than delivered to a stranger.
            info = os.fstat(descriptor)
            mine = getattr(os, "getuid", lambda: info.st_uid)()
            if not (stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == mine):
                os.close(descriptor)
                if warn is not None:
                    warn(f"refusing to write the audit log at {path}: not a file "
                         f"Slackline created")
                return
            # Never at the cost of the record: fchmod is Unix-only, and a
            # platform without it still gets its line.
            fchmod = getattr(os, "fchmod", None)
            if fchmod is not None:
                try:
                    fchmod(descriptor, 0o600)
                except OSError:
                    pass
            handle = os.fdopen(descriptor, "a", encoding="utf-8")
        except Exception:
            os.close(descriptor)
            raise
        with handle:
            handle.write(line + "\n")
    except Exception as exc:  # noqa: BLE001
        if warn is not None:
            warn(f"could not write the audit log at {path}: {scrub(exc)}")
