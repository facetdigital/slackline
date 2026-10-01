"""``slackline-read whoami``: which identity, which workspace, which scopes.

The first thing an agent should run, and the cheapest way to answer "why can it
not see that channel?" Almost always the answer is either the wrong workspace or
a missing scope, and this command shows both.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..errors import ArgumentError

HELP = """\
Usage: slackline-read whoami [--format text|json|jsonl]

Report the authenticated user, the workspace, and the scopes the token holds.
Makes exactly one Slack call (auth.test) and reads no messages.
"""


def parse(argv: List[str]) -> Dict[str, Any]:
    """whoami takes no options; anything given is an argument error."""
    if argv:
        raise ArgumentError(f"unknown option for 'whoami': {argv[0]}")
    return {}


def run(ctx, argv: List[str], options: Optional[Dict[str, Any]] = None) -> int:
    # Help is handled by dispatch, which also parsed argv once already; a
    # direct caller (the tests) may omit options. whoami has none to carry.
    if options is None:
        options = parse(argv)

    body = ctx.client.call("auth.test")

    # Slack returns the token's granted scopes in a response header rather than
    # in the body, so this is the only place they can be read from.
    header = ctx.client.last_headers or {}
    raw_scopes = header.get("X-OAuth-Scopes") or header.get("x-oauth-scopes") or ""
    scopes = [s.strip() for s in raw_scopes.split(",") if s.strip()]

    ctx.emit(
        {
            "type": "identity",
            "user": {"id": body.get("user_id"), "name": body.get("user")},
            "team": {"id": body.get("team_id"), "name": body.get("team")},
            "url": body.get("url"),
            "scopes": scopes,
            "_raw": body,
        }
    )
    return 0
