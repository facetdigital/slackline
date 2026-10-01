"""Argument parsing shared by the commands that select a conversation.

Kept in one place so ``resolve``, ``conversation``, and ``thread`` cannot drift
into accepting subtly different spellings of the same selector. An agent that
learns the flags from one command should be right about the others.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from ..errors import ArgumentError
from ..resolve import check_selectors

SELECTOR_FLAGS = ("--channel", "--with", "--conversation")
TIME_FLAGS = ("--since", "--after", "--before", "--between")


def new_selection() -> Dict[str, Any]:
    return {
        "channels": [],
        "with_users": [],
        "conversations": [],
        "since": None,
        "after": None,
        "before": None,
        "between": None,
        "timezone": None,
    }


def validate_selection(selection: Dict[str, Any]) -> None:
    """Exactly one kind of selector was given, or an ArgumentError.

    Called from each command's ``parse`` so the check runs before any
    credential is resolved: an invalid invocation is exit 2 whether or not a
    token exists (D18).
    """
    check_selectors(selection["channels"], selection["with_users"], selection["conversations"])


def take_value(argv: List[str], index: int, flag: str) -> Tuple[str, int]:
    index += 1
    if index >= len(argv):
        raise ArgumentError(f"{flag} requires a value")
    return argv[index], index


def parse_selector_arg(argv: List[str], index: int, selection: Dict[str, Any]) -> int:
    """Consume one selector or time flag. Returns the new index, or -1 if unhandled."""
    arg = argv[index]

    if arg == "--channel":
        value, index = take_value(argv, index, arg)
        selection["channels"].append(value)
    elif arg == "--with":
        value, index = take_value(argv, index, arg)
        selection["with_users"].append(value)
    elif arg == "--conversation":
        value, index = take_value(argv, index, arg)
        selection["conversations"].append(value)
    elif arg == "--since":
        selection["since"], index = take_value(argv, index, arg)
    elif arg == "--after":
        selection["after"], index = take_value(argv, index, arg)
    elif arg == "--before":
        selection["before"], index = take_value(argv, index, arg)
    elif arg == "--between":
        # Two positional values rather than one comma-joined string: a timestamp
        # can contain a comma-free but shell-hostile format, and two arguments
        # compose more predictably when an agent builds argv programmatically.
        start, index = take_value(argv, index, arg)
        end, index = take_value(argv, index, arg)
        selection["between"] = (start, end)
    elif arg == "--timezone":
        selection["timezone"], index = take_value(argv, index, arg)
    else:
        return -1
    return index
