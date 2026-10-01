"""Entry point for ``slackline-read``: the read-only half of Slackline.

Parses global flags, builds a :class:`Context`, dispatches to a command, and maps
exceptions to the exit codes in D18. It holds no Slack logic of its own.

Nothing reachable from here can mutate Slack. That is enforced by the method
allowlist in :mod:`slackline.api`, not by convention (D3).
"""
from __future__ import annotations

import os
import pathlib
import sys
import time
from typing import Any, Dict, List, Optional

from . import __version__, audit, errors
from .formatters import FORMATS, get_formatter
from .formatters.text import _clean

PROG = "slackline-read"

COMMANDS = ("whoami", "channels", "users", "resolve", "conversation", "thread")

USAGE = f"""\
{PROG} {__version__} - read-only Slack access for humans and AI agents.

Usage:
  {PROG} <command> [options]

Commands:
  whoami         Report the authenticated identity, workspace, and granted scopes
  channels       List the conversations this user can see
  users          List and search the workspace user directory
  resolve        Resolve a selector to conversation IDs without reading messages
  conversation   Read messages from a conversation over a time window
  thread         Read a root message and all of its replies

Global options:
  --format FORMAT       text (default), jsonl, json, or raw
  --token-source SRC    env, file, keychain, op, or dotenv. Forces one source and
                        disables fallthrough, so a headless job fails loudly
                        instead of blocking on an interactive prompt.
  --token-file PATH     Read the token from a file
  --refresh-cache       Rebuild the cached user and conversation directories
  --timeout SECONDS     Per-request timeout (default 30)
  -q, --quiet           Suppress progress and warnings on stderr
  -h, --help            Show this help
  --version             Show the version

Credentials are resolved in this order, first hit wins:
  1. SLACK_USER_TOKEN environment variable   <- the only one an agent needs
  2. macOS Keychain (service 'slackline')
  3. 1Password, if SLACKLINE_OP_REF is set   <- prompts for TouchID
  4. ./.env in the current directory
An explicit --token-file PATH overrides all of these: it is consulted first,
and a missing file is an error even when SLACK_USER_TOKEN is set.

There is no --token flag, deliberately: a token in argv is readable by any
process via ps and is written to shell history.

Every invocation appends one line to ~/.local/state/slackline/audit.jsonl
recording what was read: never message content. Set SLACKLINE_AUDIT_LOG to
another path to move it, or to 'none' to switch it off.

This tool is read-only. It cannot post, edit, delete, react, join, or mark
anything as read, under any combination of flags.

Run '{PROG} <command> --help' for command-specific options.
"""

EXIT_CODES = """\
Exit codes:
    0  success, including a truncated or paginated read
    1  Slack API or unexpected failure
    2  invalid arguments
    3  authentication failure or missing scope
    4  ambiguous selector
    5  not found, or not accessible to this user
    7  refused by the read-only method allowlist
  130  interrupted
"""


class Context:
    """Everything a command needs, and a place to put its records.

    Commands never write to stdout themselves. They call :meth:`emit`, and the
    chosen formatter renders once at the end. That is what keeps the stream
    discipline in D19 a property of the architecture rather than of each command
    author remembering it.
    """

    def __init__(
        self,
        client: Any = None,
        fmt: str = "text",
        quiet: bool = False,
        refresh_cache: bool = False,
        cache: Any = None,
    ):
        self.client = client
        self.fmt = fmt
        self.quiet = quiet
        self.refresh_cache = refresh_cache
        self.cache = cache
        self.records: List[Dict[str, Any]] = []
        # What the audit log will report. Accumulated as records are emitted
        # rather than read back from self.records, which flush() empties.
        self.audit: Dict[str, Any] = {"conversations": [], "window": None, "messages": 0}
        self._directory: Any = None
        self._resolver: Any = None
        self._identity: Optional[Dict[str, Any]] = None

    def identity(self) -> Dict[str, Any]:
        """Who we are, fetched once per process and reused."""
        if self._identity is None:
            self._identity = self.client.call("auth.test")
        return self._identity

    def directory(self):
        """The user and conversation index, built or loaded from cache once."""
        if self._directory is None:
            from .cache import Cache
            from .directory import Directory

            identity = self.identity()
            cache = self.cache or Cache(
                team_id=identity.get("team_id") or "unknown",
                refresh=self.refresh_cache,
                warn=self.warn,
            )
            self._directory = Directory(
                client=self.client, cache=cache, self_user_id=identity.get("user_id")
            )
        return self._directory

    def resolver(self):
        if getattr(self, "_resolver", None) is None:
            from .resolve import Resolver

            self._resolver = Resolver(
                client=self.client,
                directory=self.directory(),
                self_user_id=self.identity().get("user_id"),
                warn=self.warn,
                cache=self.directory().cache,
            )
        return self._resolver

    def emit(self, record: Dict[str, Any]) -> None:
        self.records.append(record)

        kind = record.get("type")
        if kind == "conversation":
            identifier = record.get("id")
            if identifier and identifier not in self.audit["conversations"]:
                self.audit["conversations"].append(identifier)
            if record.get("range") and self.audit["window"] is None:
                self.audit["window"] = record["range"]
        elif kind == "message":
            self.audit["messages"] += 1

    def warn(self, message: str) -> None:
        """Diagnostics go to stderr, always (D19). stderr is a terminal too, so
        the same control-character strip as the text formatter applies."""
        if not self.quiet:
            sys.stderr.write(f"{PROG}: {_clean(message)}\n")

    def help(self, text: str) -> None:
        sys.stdout.write(text)

    def flush(self) -> None:
        formatter = get_formatter(self.fmt)
        # 'raw' would otherwise leak the private _raw key into every other format.
        if self.fmt != "raw":
            records = [
                {k: v for k, v in record.items() if not k.startswith("_")}
                for record in self.records
            ]
        else:
            records = self.records
        formatter(records)
        self.records = []


def _parse_global_flags(argv: List[str]):
    """Split global flags out of argv, leaving command-specific ones behind."""
    options = {
        "format": "text",
        "token_source": None,
        "token_file": None,
        "refresh_cache": False,
        "timeout": 30.0,
        "quiet": False,
    }
    rest: List[str] = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--format":
            index += 1
            if index >= len(argv):
                raise errors.ArgumentError("--format requires a value")
            options["format"] = argv[index]
        elif arg.startswith("--format="):
            options["format"] = arg.split("=", 1)[1]
        elif arg == "--token-source":
            index += 1
            if index >= len(argv):
                raise errors.ArgumentError("--token-source requires a value")
            options["token_source"] = argv[index]
        elif arg.startswith("--token-source="):
            options["token_source"] = arg.split("=", 1)[1]
        elif arg == "--token-file":
            index += 1
            if index >= len(argv):
                raise errors.ArgumentError("--token-file requires a value")
            options["token_file"] = pathlib.Path(argv[index])
        elif arg.startswith("--token-file="):
            options["token_file"] = pathlib.Path(arg.split("=", 1)[1])
        elif arg == "--refresh-cache":
            options["refresh_cache"] = True
        elif arg == "--timeout" or arg.startswith("--timeout="):
            if arg.startswith("--timeout="):
                raw = arg.split("=", 1)[1]
            else:
                index += 1
                if index >= len(argv):
                    raise errors.ArgumentError("--timeout requires a value")
                raw = argv[index]
            try:
                options["timeout"] = float(raw)
            except ValueError:
                raise errors.ArgumentError(
                    f"--timeout expects a number of seconds, got {raw!r}"
                ) from None
            if options["timeout"] <= 0:
                raise errors.ArgumentError("--timeout must be greater than zero")
        elif arg in ("-q", "--quiet"):
            options["quiet"] = True
        else:
            rest.append(arg)
        index += 1

    if options["format"] not in FORMATS:
        raise errors.ArgumentError(
            f"unknown --format '{options['format']}'. Expected one of: "
            + ", ".join(FORMATS)
        )
    return options, rest


def _load_command(name: str):
    from importlib import import_module

    try:
        return import_module(f".commands.{name}", package=__package__)
    except ImportError as exc:
        # A command listed in COMMANDS but not yet implemented must report that
        # clearly, not crash with a module-not-found traceback.
        raise errors.ArgumentError(
            f"command '{name}' is not available in this build ({errors.scrub(exc)})"
        ) from None


def main(argv: Optional[List[str]] = None) -> int:
    """Run one invocation and record it (D20).

    The audit wraps dispatch rather than living inside it so that every exit
    path is covered by construction, including the ones that never reach a
    command: an unknown flag, a missing credential, an interrupt. An audit log
    with a hole in it for exactly the failures is not an audit log.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    started = time.monotonic()
    box: Dict[str, Any] = {}

    code = _dispatch(argv, box)

    ctx = box.get("ctx")
    summary = ctx.audit if ctx is not None else {}
    audit.record(
        path=audit.resolve_path(),
        command=box.get("command"),
        argv=argv,
        conversations=summary.get("conversations") or [],
        window=summary.get("window"),
        messages=summary.get("messages") or 0,
        duration_ms=int((time.monotonic() - started) * 1000),
        exit_code=code,
        warn=ctx.warn if ctx is not None else None,
    )
    return code


def _dispatch(argv: List[str], box: Dict[str, Any]) -> int:
    if not argv:
        # No command is a usage error, not a request for help. Usage goes to
        # stderr so stdout carries only requested data (D19).
        sys.stderr.write(USAGE)
        return 2
    if argv[0] in ("-h", "--help", "help"):
        sys.stdout.write(USAGE + "\n" + EXIT_CODES)
        return 0
    if argv[0] in ("--version", "-V"):
        sys.stdout.write(f"{PROG} {__version__}\n")
        return 0

    command_name, remainder = argv[0], argv[1:]
    box["command"] = command_name
    if command_name not in COMMANDS:
        sys.stderr.write(f"{PROG}: unknown command '{command_name}'\n\n" + USAGE)
        return 2

    ctx = None
    try:
        options, rest = _parse_global_flags(remainder)
        ctx = Context(
            fmt=options["format"],
            quiet=options["quiet"],
            refresh_cache=options["refresh_cache"],
        )
        box["ctx"] = ctx

        command = _load_command(command_name)

        # --help must not require a credential: an agent discovering the tool
        # should be able to read every help page with no token at all.
        #
        # Help is handled entirely here rather than inside each command. When
        # both layers checked, they disagreed about where the flag could appear:
        # this one scanned all of argv, the commands looked only at argv[0]. A
        # command invoked as 'whoami foo --help' therefore skipped credential
        # resolution and then dereferenced a client that was never built.
        if any(arg in ("-h", "--help") for arg in rest):
            ctx.help(command.HELP)
            return 0

        # Arguments before credentials. D18 says 2 means invalid arguments,
        # and that must not depend on whether a token is present: an agent with
        # a bad flag is told "bad argument", never "no token". It also keeps the
        # offline suite honest, since an argument error never reaches the
        # credential chain (invariant 10).
        options_for_command = command.parse(rest)

        from .api import SlackClient
        from .auth import resolve_token

        resolved = resolve_token(
            source=options["token_source"], token_file=options["token_file"]
        )
        ctx.client = SlackClient(
            token=resolved.token,
            timeout=options["timeout"],
            on_wait=ctx.warn,
        )

        code = command.run(ctx, rest, options_for_command)
        ctx.flush()
        # Flush inside the guarded block. stdout is buffered, so a closed
        # downstream pipe usually surfaces at interpreter shutdown, long past
        # any handler here, and Python then exits 120 with a traceback.
        sys.stdout.flush()
        return code

    except errors.SlacklineError as exc:
        sys.stderr.write(f"{PROG}: {_clean(exc.report())}\n")
        return exc.exit_code
    except BrokenPipeError:
        # Downstream closed the pipe, as `head` does. Not an error, and the
        # README's own example is a piped read. Redirecting the fd to devnull
        # stops the interpreter's shutdown flush from failing again and
        # overriding this exit code with 120.
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except Exception:  # noqa: BLE001
            # sys.stdout is not always a real file: it may be a StringIO under
            # a test harness, or a wrapper with no fileno at all. Failing to
            # redirect is not a reason to turn a successful read into a crash.
            pass
        return 0
    except KeyboardInterrupt:
        # 130 by shell convention, so a harness can tell an interrupted run
        # from an API failure, which is what 1 means (D18).
        sys.stderr.write(f"\n{PROG}: interrupted\n")
        return 130
    except Exception as exc:  # noqa: BLE001
        # Scrub before rendering: an unexpected traceback must not be the way a
        # token escapes (D4b).
        sys.stderr.write(f"{PROG}: unexpected failure: {_clean(errors.scrub(exc))}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
