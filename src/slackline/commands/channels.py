"""``slackline-read channels``: what this user can actually read.

Exists so an agent never has to be handed a channel ID. Discovery is part of
being usable headlessly: a tool that requires you to already know the answer is
not a tool an agent can start from.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..errors import ArgumentError

KINDS = ("public", "private", "im", "mpim")

HELP = """\
Usage: slackline-read channels [options]

List every conversation the authenticated user can read.

Options:
  --kind KINDS     Comma-separated: public, private, im, mpim. Default: all
  --match TEXT     Only conversations whose name or label contains TEXT
  --include-archived   Also list archived conversations. Default: hidden
  --format text|jsonl|json

Group DM names are not unique. When two rows share a name they are two distinct
conversations, and reading one is not the same as reading the other. Select them
by ID.
"""


def parse(argv: List[str]) -> Dict[str, Any]:
    """Every argument check, and nothing that needs a client (D18)."""
    kinds = set(KINDS)
    match = None
    include_archived = False

    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--kind":
            index += 1
            if index >= len(argv):
                raise ArgumentError("--kind requires a value")
            requested = [k.strip() for k in argv[index].split(",") if k.strip()]
            unknown = [k for k in requested if k not in KINDS]
            if unknown:
                raise ArgumentError(
                    f"unknown --kind {unknown[0]!r}. Expected one or more of: "
                    + ", ".join(KINDS)
                )
            kinds = set(requested)
        elif arg == "--match":
            index += 1
            if index >= len(argv):
                raise ArgumentError("--match requires a value")
            match = argv[index].lower()
        elif arg == "--include-archived":
            include_archived = True
        else:
            raise ArgumentError(f"unknown option for 'channels': {arg}")
        index += 1
    return {"kinds": kinds, "match": match, "include_archived": include_archived}


def run(ctx, argv: List[str], options: Optional[Dict[str, Any]] = None) -> int:
    # Help is handled by dispatch, which also parsed argv once already; a
    # direct caller (the tests) may omit options.
    if options is None:
        options = parse(argv)
    kinds, match, include_archived = (
        options["kinds"], options["match"], options["include_archived"]
    )

    rows = []
    for conversation in ctx.directory().conversations():
        if conversation["kind"] not in kinds:
            continue
        if conversation["archived"] and not include_archived:
            continue
        if match and match not in (conversation["name"] + " " + conversation["label"]).lower():
            continue
        rows.append(dict(conversation, type="conversation"))

    rows.sort(key=lambda r: (r["kind"], r["label"].lower()))
    for row in rows:
        ctx.emit(row)
    return 0
