"""On-disk cache for the user and conversation directories (D7).

This is mandatory rather than an optimization. Resolving ``@joan`` requires the
user directory, and building that means paginating ``users.list`` across the
workspace. The measured conversation inventory alone is 398 rows over 3 pages.
Doing that on every invocation would make a one-second tool a ten-second tool,
and agents call these tools in loops.

What is cached: names, IDs, handles, membership. Directory data.

What is never cached: message content. Slackline reads on demand and forgets.
Caching messages would turn a stateless tool into a stale datastore and put Slack
content on disk. Warehousing Slack history is a different tool's job.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import time
from typing import Any, Callable, Dict, Optional

#: Cacheable directory kinds. Anything else is refused, so "just cache the
#: messages too" cannot happen by accident in a later change.
KINDS = ("users", "conversations", "members", "external-users")

USERS_TTL = 24 * 3600
CONVERSATIONS_TTL = 3600  # conversations churn faster than people do
MEMBERS_TTL = 24 * 3600   # group DM membership is immutable in Slack
EXTERNAL_USERS_TTL = USERS_TTL  # people from shared channels' other workspaces


def default_root() -> pathlib.Path:
    override = os.environ.get("SLACKLINE_CACHE_DIR")
    if override:
        return pathlib.Path(override).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return pathlib.Path(xdg).expanduser() / "slackline"
    return pathlib.Path.home() / ".cache" / "slackline"


class Cache:
    """A tiny TTL cache of JSON documents, partitioned by workspace.

    Two JSON files with atomic replacement, rather than SQLite. A schema and a
    migration story would be pure cost here.
    """

    def __init__(
        self,
        root: Optional[pathlib.Path] = None,
        team_id: str = "unknown",
        refresh: bool = False,
        warn: Optional[Callable[[str], None]] = None,
    ):
        # The value comes from Slack's auth.test, not from the caller, but a
        # path component is never built from an unchecked string (D7).
        if not re.fullmatch(r"[A-Za-z0-9_-]+", team_id or ""):
            raise ValueError(f"refusing to use {team_id!r} as a cache key")
        self.root = pathlib.Path(root) if root else default_root()
        self.team_id = team_id
        self.refresh = refresh
        self.warn = warn or (lambda _message: None)
        self._warned_about_root = False

    def path_for(self, kind: str) -> pathlib.Path:
        # Keying by team ID means one workspace's directory can never answer
        # another workspace's question.
        return self.root / f"{kind}-{self.team_id}.json"

    def _ensure_root(self) -> None:
        # 0o700: the cache names colleagues and their email addresses. It has no
        # business being world-readable on a shared machine. But only a
        # directory this process created is tightened: SLACKLINE_CACHE_DIR may
        # be $HOME or a shared checkout, and a read-only tool does not change
        # permissions it did not set. Whether it was created is decided by
        # mkdir itself, the same rule the audit log follows (D7, D20).
        self.root.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.mkdir(self.root, 0o700)
        except FileExistsError:
            mode = os.stat(self.root).st_mode & 0o777
            if mode & 0o077 and not self._warned_about_root:
                self._warned_about_root = True
                self.warn(
                    f"cache directory {self.root} is not owner-only (mode {mode:o}). "
                    f"It names colleagues, and another account that can write it "
                    f"decides what a name resolves to. Consider chmod 700."
                )
        else:
            try:
                os.chmod(self.root, 0o700)
            except OSError:
                pass

    def get(self, kind: str, max_age: float) -> Optional[Any]:
        """Return the cached payload, or None on a miss, an expiry, or corruption."""
        if self.refresh:
            return None
        path = self.path_for(kind)
        # A link is never read: a poisoned document behind a symlink in a shared
        # root would decide which conversation a selector resolves to.
        if path.is_symlink():
            return None
        try:
            raw = json.loads(path.read_text())
        except (OSError, ValueError):
            # A corrupt or absent cache is a miss, never a crash. The worst case
            # is one slow invocation that rewrites it.
            return None
        if not isinstance(raw, dict) or "cached_at" not in raw:
            return None
        if time.time() - raw["cached_at"] > max_age:
            return None
        if raw.get("team_id") != self.team_id:
            return None
        return raw.get("payload")

    def put(self, kind: str, payload: Any) -> None:
        """Write atomically, so a killed process cannot leave a truncated cache."""
        if kind not in KINDS:
            raise ValueError(
                f"refusing to cache '{kind}': slackline caches directory data only "
                f"({', '.join(KINDS)}). Message content is never written to disk."
            )
        self._ensure_root()
        path = self.path_for(kind)
        tmp = path.with_suffix(".tmp")
        document = {"team_id": self.team_id, "cached_at": time.time(), "payload": payload}
        # Never through a symlink, the same rule as the audit log: a link
        # planted at either name in a directory another account can write
        # would turn a read-only tool into an arbitrary-file writer. The final
        # name is refused with a warning; the temp name is taken over, because
        # a stale temp file is also what a crashed write leaves behind.
        if path.is_symlink():
            self.warn(f"refusing to write the cache through a symlink at {path}")
            return
        try:
            if tmp.is_symlink() or tmp.exists():
                tmp.unlink()
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(tmp, flags, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(document, ensure_ascii=False))
            os.replace(tmp, path)
        except OSError as exc:
            # A cache that cannot be written is a slow run, not a failed one.
            self.warn(f"could not write the cache at {path}: {exc}")
        finally:
            # Whether replace succeeded, was interrupted, or raised, no temp file
            # is left behind to be mistaken for a real cache later.
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    def invalidate(self, kind: str) -> None:
        try:
            self.path_for(kind).unlink()
        except OSError:
            pass
