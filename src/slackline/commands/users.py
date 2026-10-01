"""``slackline-read users``: the workspace directory, for resolving people.

Bots and deactivated accounts are excluded by default because they are noise when
a human is looking for a colleague, and included on request because they are
essential when a transcript mentions one.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..errors import ArgumentError

HELP = """\
Usage: slackline-read users [options]

List and search the workspace user directory.

Options:
  --match TEXT         Only users whose handle, real name, or display name matches
  --include-bots       Include bot users (excluded by default)
  --include-deleted    Include deactivated accounts (excluded by default)
  --format text|jsonl|json
"""


def parse(argv: List[str]) -> Dict[str, Any]:
    """Every argument check, and nothing that needs a client (D18)."""
    match = None
    include_bots = False
    include_deleted = False

    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--match":
            index += 1
            if index >= len(argv):
                raise ArgumentError("--match requires a value")
            match = argv[index]
        elif arg == "--include-bots":
            include_bots = True
        elif arg == "--include-deleted":
            include_deleted = True
        else:
            raise ArgumentError(f"unknown option for 'users': {arg}")
        index += 1
    return {"match": match, "include_bots": include_bots, "include_deleted": include_deleted}


def run(ctx, argv: List[str], options: Optional[Dict[str, Any]] = None) -> int:
    # Help is handled by dispatch, which also parsed argv once already; a
    # direct caller (the tests) may omit options.
    if options is None:
        options = parse(argv)
    match, include_bots, include_deleted = (
        options["match"], options["include_bots"], options["include_deleted"]
    )

    directory = ctx.directory()
    if match:
        candidates = directory.find_users(match)
    else:
        candidates = list(directory.users().values())

    rows = []
    for user in candidates:
        if user["is_bot"] and not include_bots:
            continue
        if user["deleted"] and not include_deleted:
            continue
        rows.append(dict(user, type="user"))

    rows.sort(key=lambda r: r["handle"].lower())
    for row in rows:
        ctx.emit(row)
    return 0
