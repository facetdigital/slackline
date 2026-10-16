"""``slackline-read resolve``: what would be read, without reading it.

Three jobs, in order of how often they matter:

1. **Disambiguation.** Everywhere else an ambiguous selector is a hard failure.
   This is where a caller inspects the candidates and picks one.
2. **A cheap dry run.** "Which conversation does --with @joan --with @kim
   actually mean?" answered in one or two cheap calls, rather than after a
   500-message pull from the wrong place.
3. **Debuggability.** When output looks wrong, this separates a selection bug
   from a retrieval bug.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..errors import ArgumentError
from ._common import new_selection, parse_selector_arg, validate_selection

HELP = """\
Usage: slackline-read resolve [selector]

Resolve a selector to conversation IDs. Reads no messages.

Selectors (use exactly one kind):
  --channel NAME_OR_ID   Repeatable
  --with PERSON          Repeatable. A group DM is matched by its member set,
                         never by its name, because Slack's group DM names are
                         neither unique nor kept current. PERSON is @handle
                         for exactly that person; a bare name searches names
                         and fails if two people match.
  --conversation ID      Repeatable

Options:
  --format text|json|jsonl

The members list of a group DM is the verified member set and includes you. The
members list of a one-to-one DM is the other person only.

Exit codes:
  0  resolved
  4  ambiguous: the candidates are printed, select one by ID
  5  no such conversation, or not visible to you
"""


def parse(argv: List[str]) -> Dict[str, Any]:
    """Every argument check, and nothing that needs a client (D18)."""
    selection = new_selection()
    index = 0
    while index < len(argv):
        consumed = parse_selector_arg(argv, index, selection)
        if consumed < 0:
            raise ArgumentError(f"unknown option for 'resolve': {argv[index]}")
        index = consumed + 1
    validate_selection(selection)
    return {"selection": selection}


def run(ctx, argv: List[str], options: Optional[Dict[str, Any]] = None) -> int:
    if options is None:
        options = parse(argv)
    selection = options["selection"]

    matches = ctx.resolver().resolve(
        channels=selection["channels"] or None,
        with_users=selection["with_users"] or None,
        conversations=selection["conversations"] or None,
    )

    ctx.emit(
        {
            "type": "resolution",
            "selector": {
                key: selection[key]
                for key in ("channels", "with_users", "conversations")
                if selection[key]
            },
            "matches": [
                {
                    "id": match["id"],
                    "kind": match["kind"],
                    "name": match.get("name") or None,
                    "label": match.get("label"),
                    "archived": bool(match.get("archived")),
                    "members": sorted(
                        ctx.directory().handle_for(user_id)
                        for user_id in (match.get("members") or [])
                    ),
                }
                for match in matches
            ],
        }
    )
    return 0
