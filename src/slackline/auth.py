"""Credential resolution: environment first, humans second, prompts never by accident.

The ordering in :data:`SOURCE_ORDER` is the whole design (D4a). A headless agent
needs only ``SLACK_USER_TOKEN``, and it is checked first, so an agent can never
fall through into a source that blocks on a human.

Slackline's house rule is that secrets resolve through 1Password with TouchID
gating. That rule exists to stop a background process from silently grabbing a
secret, and it is honored here rather than bypassed: a Slackline process started
without a token in its environment has no file to reach for and must fall through
to ``op``, which prompts. The runner passes the secret in; the tool never goes
hunting for one on its own initiative.

There is deliberately no ``--token`` flag (PRD P18). Values in argv are readable
by every process on the machine via ``ps`` and are written to shell history.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
from typing import Callable, Dict, List, NamedTuple, Optional

from .errors import ArgumentError, AuthError

ENV_VAR = "SLACK_USER_TOKEN"
KEYCHAIN_SERVICE = "slackline"
OP_REF_VAR = "SLACKLINE_OP_REF"
OP_ACCOUNT_VAR = "SLACKLINE_OP_ACCOUNT"

SOURCE_ORDER: List[str] = ["env", "file", "keychain", "op", "dotenv"]


class ResolvedToken(NamedTuple):
    token: str
    source: str


# -- individual sources -----------------------------------------------------


def _read_from_env() -> Optional[str]:
    return os.environ.get(ENV_VAR) or None


def _read_from_file(path: Optional[pathlib.Path]) -> Optional[str]:
    if not path:
        return None
    path = pathlib.Path(path)
    if not path.exists():
        raise AuthError(f"--token-file {path} does not exist")
    return path.read_text().strip() or None


def _read_from_keychain() -> Optional[str]:
    """macOS Keychain. A convenience for humans, never a headless dependency."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _read_from_op(ref: str, account: Optional[str]) -> Optional[str]:
    """1Password. Prompts for TouchID, so it is only ever reached deliberately."""
    command = ["op", "read"]
    if account:
        command += ["--account", account]
    command.append(ref)
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def parse_dotenv(path: pathlib.Path) -> Dict[str, str]:
    """Minimal ``.env`` reader. Not a general parser, and deliberately not one."""
    values: Dict[str, str] = {}
    try:
        text = pathlib.Path(path).read_text()
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _read_from_dotenv(path: Optional[pathlib.Path]) -> Optional[str]:
    return parse_dotenv(path or pathlib.Path(".env")).get(ENV_VAR) or None


# -- validation -------------------------------------------------------------


def validate_token(token: str) -> str:
    """Reject anything that is not a Slack user token, with a usable message.

    A bot token is rejected rather than passed through. Slackline's premise is
    that it acts as the authenticated human, with exactly that person's access; a
    bot token would silently give it a different and usually narrower view, and
    the resulting 'channel not found' errors would be baffling.
    """
    token = (token or "").strip()
    if not token:
        raise AuthError("empty token")
    if token.startswith("xoxb-"):
        raise AuthError(
            "that is a bot token. Slackline needs a user token (xoxp-) because it "
            "acts as you, with your access and nothing more."
        )
    if token.startswith("xapp-"):
        raise AuthError(
            "that is an app-level token. Slackline needs a user token (xoxp-)."
        )
    if not token.startswith("xoxp-"):
        raise AuthError(
            "that does not look like a Slack user token. Expected a value starting "
            "with 'xoxp-'."
        )
    return token


# -- the chain --------------------------------------------------------------


def resolve_token(
    source: Optional[str] = None,
    token_file: Optional[pathlib.Path] = None,
    dotenv_path: Optional[pathlib.Path] = None,
) -> ResolvedToken:
    """Resolve a token from the first source that yields one.

    ``source`` forces exactly one source and disables fallthrough, so a headless
    job fails loudly instead of blocking on a prompt nobody will answer.
    """
    if source is not None and source not in SOURCE_ORDER:
        raise ArgumentError(
            f"unknown --token-source '{source}'. Expected one of: "
            + ", ".join(SOURCE_ORDER)
        )

    op_ref = os.environ.get(OP_REF_VAR)
    op_account = os.environ.get(OP_ACCOUNT_VAR)

    readers: Dict[str, Callable[[], Optional[str]]] = {
        "env": _read_from_env,
        "file": lambda: _read_from_file(token_file),
        "keychain": _read_from_keychain,
        # Only reachable when explicitly configured. Without SLACKLINE_OP_REF this
        # yields None without running op, so nothing ever pops a TouchID prompt
        # that the caller did not ask for.
        "op": (lambda: _read_from_op(op_ref, op_account)) if op_ref else (lambda: None),
        "dotenv": lambda: _read_from_dotenv(dotenv_path),
    }

    if source:
        order = [source]
    elif token_file is not None:
        # An explicit --token-file is a deliberate instruction, almost always
        # meant to override whatever credential is already in the environment.
        # Leaving it at its usual position in the chain would silently do the
        # opposite of what the caller asked, and would also make the
        # "file does not exist" error unreachable whenever SLACK_USER_TOKEN
        # happened to be set.
        order = ["file"] + [name for name in SOURCE_ORDER if name != "file"]
    else:
        order = SOURCE_ORDER
    for name in order:
        try:
            value = readers[name]()
        except AuthError:
            raise
        except Exception:
            # A source being unavailable is normal: no keychain on Linux, no op
            # installed, an unreadable file. Skip it and keep going.
            continue
        if value:
            return ResolvedToken(validate_token(value), name)

    if source:
        raise AuthError(
            f"no Slack token found in the '{source}' source, and --token-source "
            f"disables the fallback chain."
        )
    raise AuthError(
        "no Slack user token found. Set the SLACK_USER_TOKEN environment variable:\n"
        "  export SLACK_USER_TOKEN='xoxp-...'\n"
        "Other sources tried, in order: macOS Keychain (service 'slackline'), "
        "1Password (set SLACKLINE_OP_REF), then ./.env. An explicit --token-file "
        "is consulted first when given."
    )
