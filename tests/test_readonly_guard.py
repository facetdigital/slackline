"""The guard that keeps 'slackline-read cannot write' true over time (D3).

Scans the package for Slack method names and asserts every one is allowlisted, so
a future command cannot quietly widen the tool's authority.

It scans the **AST**, not the raw text. A Slack method only has effect when it is
a string literal handed to the client, and the AST contains exactly those while
being structurally blind to comments. That means the architecture docs and the
code can both freely explain why ``conversations.open`` was rejected without
tripping a guard or needing a suppression marker. Docstrings are excluded for the
same reason: prose, not calls.

A guard that fires on prose gets muted, and a muted guard protects nothing.
"""
from __future__ import annotations

import ast
import pathlib
import re

from slackline import api

PACKAGE_ROOT = pathlib.Path(api.__file__).parent

# The Slack Web API namespaces. Matching a known vocabulary rather than any
# dotted word keeps 'application/json' from registering as a false positive.
SLACK_NAMESPACES = (
    "admin|api|apps|auth|bookmarks|bots|calls|canvases|chat|conversations|dialog|dnd|"
    "emoji|files|functions|migration|oauth|openid|pins|reactions|reminders|rtm|search|"
    "stars|team|tooling|usergroups|users|views|workflows"
)
METHOD_RE = re.compile(r"^(?:" + SLACK_NAMESPACES + r")\.[a-z][a-zA-Z0-9.]*$")


def _docstrings(tree: ast.AST):
    """Every docstring node in the tree. Prose, not calls."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    found.add(id(body[0].value))
    return found


def methods_in_source(source: str, label: str = "<source>"):
    """Return {method_name: line_number} for Slack methods used as literals."""
    tree = ast.parse(source, filename=label)
    docstrings = _docstrings(tree)
    found = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in docstrings:
            continue
        if METHOD_RE.match(node.value):
            found.setdefault(node.value, node.lineno)
    return found


def _scan_package():
    found = {}
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        for method, lineno in methods_in_source(path.read_text(), str(path)).items():
            found.setdefault(method, (path, lineno))
    return found


def test_every_slack_method_in_the_package_is_read_only():
    violations = []
    for method, (path, lineno) in _scan_package().items():
        if method not in api.READ_ONLY_METHODS:
            rel = path.relative_to(PACKAGE_ROOT.parent.parent)
            violations.append(f"{rel}:{lineno} uses '{method}', which is not read-only")
    assert not violations, (
        "slackline-read must never reference a Slack method outside the read-only "
        "allowlist:\n  " + "\n  ".join(violations)
    )


def test_the_scan_actually_finds_methods():
    """A guard that matches nothing would pass forever and prove nothing."""
    found = _scan_package()
    assert "conversations.history" in found
    assert "auth.test" in found
    assert len(found) >= 5


def test_the_guard_catches_a_planted_violation():
    """Proves the guard fails when it should, without editing shipped code."""
    found = methods_in_source('def send():\n    client.call("chat.postMessage")\n')
    assert "chat.postMessage" in found
    assert "chat.postMessage" not in api.READ_ONLY_METHODS


def test_a_comment_mentioning_a_method_does_not_trip_the_guard():
    """Explaining why a method is banned must not be punished."""
    source = (
        "# conversations.open can create a DM, so it is banned.\n"
        'client.call("conversations.history")\n'
    )
    found = methods_in_source(source)
    assert "conversations.open" not in found
    assert "conversations.history" in found


def test_a_docstring_mentioning_a_method_does_not_trip_the_guard():
    source = '"""We use users.conversations, not conversations.list."""\n'
    assert methods_in_source(source) == {}


def test_a_real_call_beside_a_comment_still_trips_the_guard():
    """The exemption is structural, so it cannot be abused to hide a call."""
    source = 'client.call("chat.delete")  # conversations.open is banned\n'
    found = methods_in_source(source)
    assert "chat.delete" in found, "a comment must not launder the call beside it"


def test_a_filename_is_not_mistaken_for_a_method():
    """'users.py' matches the namespace pattern but is not a method.

    The AST approach handles this for free: a filename in a comment is not a
    string literal, and the anchored pattern rejects the '.py' suffix anyway.
    """
    found = methods_in_source('# see users.py\nclient.call("users.list")\n')
    assert "users.py" not in found
    assert "users.list" in found


def test_the_allowlist_contains_no_mutating_method():
    """Belt and braces: assert the known-dangerous families are absent."""
    for method in api.READ_ONLY_METHODS:
        assert not method.startswith(
            ("chat.", "reactions.", "files.", "admin.", "pins.", "stars.")
        ), f"{method} can mutate Slack"
    for method in (
        "conversations.open",
        "conversations.join",
        "conversations.mark",
        "conversations.leave",
        "conversations.create",
    ):
        assert method not in api.READ_ONLY_METHODS
