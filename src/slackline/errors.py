"""Exception hierarchy, exit-code mapping, and token redaction.

Two jobs, both contracts rather than conveniences:

1. Every failure maps to a stable exit code (D18) that an agent can branch on.
2. No token reaches output, ever, including through a traceback (D4b, PRD P14).

Exit code 6 is deliberately unassigned. The seed proposal used it for partial
results; Slackline exits 0 on a successful but truncated read and reports the
truncation in the payload (D16). Leaving 6 unclaimed means that meaning cannot
creep back in.
"""
from __future__ import annotations

import re
from typing import Any, List, Optional, Sequence

# Slack credential prefixes. "xox?-" covers user (xoxp), bot (xoxb), legacy
# workspace (xoxa), refresh (xoxe), and session (xoxc/xoxd/xoxs) tokens; "xapp-"
# covers app-level tokens, which do not share the xox prefix and would otherwise
# leak. Matching on prefix rather than an exact length keeps this working if
# Slack changes token formats again.
#
# The character class deliberately includes URL-encoding and base64 characters.
# xoxd cookie values are percent-encoded and contain '%', '/', '+' and '=';
# a class of [A-Za-z0-9-] would truncate at the first of those and leave the
# remainder of the secret in the output, which is worse than not redacting at
# all because it looks redacted.
_TOKEN_RE = re.compile(r"(?:xox[a-z]|xapp)-[A-Za-z0-9%/+=._-]+", re.IGNORECASE)

_REDACTION = "xox***"  # one visible marker for every credential shape


def scrub(value: Any) -> str:
    """Return ``value`` as a string with any token-shaped substring redacted.

    Applied to every error message before it can be rendered. Accepts non-string
    input because exception args are not always strings.
    """
    text = value if isinstance(value, str) else str(value)
    return _TOKEN_RE.sub(_REDACTION, text)


class SlacklineError(Exception):
    """Base for every Slackline failure. Carries the process exit code."""

    exit_code = 1

    def __str__(self) -> str:
        return scrub(super().__str__())

    def report(self) -> str:
        """The full text to write to stderr. Subclasses may add detail."""
        return str(self)


class SlackApiError(SlacklineError):
    """Slack returned ``ok: false``, or the transport failed."""

    exit_code = 1


class ArgumentError(SlacklineError):
    """The invocation was malformed: bad flag, missing selector, inverted range."""

    exit_code = 2


class AuthError(SlacklineError):
    """No usable credential, an invalid token, or a scope the token lacks."""

    exit_code = 3


class AmbiguousSelector(SlacklineError):
    """A selector matched more than one thing.

    Never resolved by guessing (D8). The candidates travel with the exception so
    the caller, human or agent, can retry with something unambiguous.
    """

    exit_code = 4

    def __init__(self, message: str, candidates: Optional[Sequence[str]] = None):
        super().__init__(message)
        self.candidates: List[str] = list(candidates or [])

    def report(self) -> str:
        lines = [str(self)]
        if self.candidates:
            lines.append("candidates:")
            lines.extend("  " + scrub(c) for c in self.candidates)
        return "\n".join(lines)


class NotFound(SlacklineError):
    """The target does not exist, or is not visible to this user.

    Slack frequently cannot distinguish the two, and neither can we. Saying so is
    more honest than guessing which it was.
    """

    exit_code = 5


class ForbiddenMethod(SlacklineError):
    """A Slack method outside the read-only allowlist was requested.

    This should be unreachable in shipped code; the guard test exists to keep it
    that way. If it ever fires at runtime it means the allowlist stopped one, which
    is the system working (D3).
    """

    exit_code = 7

    def __init__(self, method: str):
        super().__init__(
            f"refusing to call '{method}': not in the read-only method allowlist. "
            f"slackline-read cannot mutate Slack."
        )
        self.method = method
