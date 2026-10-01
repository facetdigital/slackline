"""HTTP transport for the Slack Web API, and the allowlist that bounds it.

The allowlist (D3) is the load-bearing part of this module. Every request passes
through :meth:`SlackClient.call`, which refuses any method outside
``READ_ONLY_METHODS`` before opening a socket. This is defense in depth: the
working token holds no write scope, so Slack would refuse a mutation anyway. The
allowlist means we do not depend on that staying true.

No third-party HTTP client, by decision D1. ``urllib`` is enough for HTTPS with
JSON bodies, and it keeps the package installable anywhere Python exists.
"""
from __future__ import annotations

import json
import random
import time
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Iterator, List, NamedTuple, Optional

from . import __version__
from .errors import AuthError, ForbiddenMethod, NotFound, SlackApiError

BASE_URL = "https://slack.com/api/"
USER_AGENT = f"slackline/{__version__} (read-only)"

DEFAULT_TIMEOUT = 30.0
MAX_ATTEMPTS = 5
BASE_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 32.0
#: Retry-After is honored exactly up to this, and refused beyond it. Without a
#: ceiling a broken or hostile intermediary could park an agent indefinitely
#: with one header (D17).
MAX_RETRY_AFTER_SECONDS = 300.0
#: Pages one paginate() call may follow. The directory and membership loops
#: had no ceiling, so a server returning a cursor with nothing behind it could
#: hold every command through users.list. Announced through on_wait when hit,
#: so an incomplete directory is never mistaken for a complete one.
MAX_PAGES_PER_CALL = 100

#: Every Slack method ``slackline-read`` is permitted to call.
#:
#: Adding to this set widens the authority of a tool whose entire premise is that
#: it cannot mutate Slack. Do not add a method here without a corresponding
#: architecture decision. ``tests/test_readonly_guard.py`` fails if any method
#: name appears in the package that is not a member of this set.
READ_ONLY_METHODS = frozenset(
    {
        "auth.test",
        "conversations.history",
        "conversations.info",
        "conversations.members",
        "conversations.replies",
        "team.info",
        "users.conversations",
        "users.info",
        "users.list",
    }
)

# Slack error strings that mean "not visible to you", mapped to exit 5 rather
# than a generic API failure. Slack often cannot distinguish "does not exist"
# from "you cannot see it", and neither can we.
_NOT_FOUND_ERRORS = {
    "channel_not_found",
    "thread_not_found",
    "user_not_found",
    "users_not_found",
    "message_not_found",
    "not_in_channel",
}

_AUTH_ERRORS = {
    "invalid_auth",
    "not_authed",
    "account_inactive",
    "token_revoked",
    "token_expired",
    "missing_scope",
    "not_allowed_token_type",
    "no_permission",
}


class Headers(Dict[str, str]):
    """Case-insensitive HTTP headers.

    ``dict(response.headers)`` throws away urllib's case-insensitive
    ``HTTPMessage``, and HTTP header names are case-insensitive by spec. Slack
    sends ``Retry-After``, but a proxy in the middle may not preserve the casing,
    and hand-coding two spellings at each call site only postpones the bug.
    Getting this wrong means silently ignoring a rate-limit delay Slack asked for.
    """

    def __init__(self, source=None):
        super().__init__()
        for key, value in dict(source or {}).items():
            self[key] = value

    def __setitem__(self, key: str, value: str) -> None:
        super().__setitem__(key.lower(), value)

    def get(self, key: str, default=None):  # type: ignore[override]
        return super().get(key.lower(), default)

    def __getitem__(self, key: str) -> str:
        return super().__getitem__(key.lower())

    def __contains__(self, key: object) -> bool:
        return super().__contains__(str(key).lower())


class TransportError(Exception):
    """A network-level failure. Retryable, unlike a malformed Slack response."""


class Response(NamedTuple):
    status: int
    body: Dict[str, Any]
    headers: Headers


class Page(NamedTuple):
    """One page of a paginated result, plus the cursor that follows it."""

    items: List[Any]
    next_cursor: str
    body: Dict[str, Any]


def _decode(payload: bytes, status: int) -> Dict[str, Any]:
    """Parse a JSON body, tolerating one that is not JSON at all.

    A 200 response carrying HTML is a real situation: a captive portal or a
    corporate proxy returning an interstitial. Letting json.JSONDecodeError
    escape turns that into an opaque "unexpected failure" instead of something
    the caller can understand.
    """
    try:
        return json.loads(payload or b"{}")
    except ValueError:
        snippet = payload[:200].decode("utf8", "replace").strip()
        raise SlackApiError(
            f"Slack returned HTTP {status} with a body that is not JSON. "
            f"This usually means something intercepted the request "
            f"(a proxy or captive portal). First bytes: {snippet!r}"
        ) from None


def _urllib_transport(url: str, headers: Dict[str, str], timeout: float) -> Response:
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as raw:
            return Response(raw.status, _decode(raw.read(), raw.status), Headers(raw.headers))
    except urllib.error.HTTPError as exc:
        # An HTTP error still carries headers we need, notably Retry-After.
        try:
            body = json.loads(exc.read() or b"{}")
        except (ValueError, OSError):
            body = {}
        return Response(exc.code, body, Headers(exc.headers or {}))
    except urllib.error.URLError as exc:
        # Raised, not returned, so the retry loop can see it. A DNS blip or a
        # connection reset is exactly the transient failure the retry policy
        # exists for; converting it to a final error here would give network
        # failures zero retries while an HTTP 500 got five.
        raise TransportError(str(exc.reason)) from None
    except (socket.timeout, TimeoutError) as exc:
        raise TransportError(f"timed out after {timeout:g}s") from None
    except OSError as exc:
        raise TransportError(str(exc)) from None


def _encode_param(value: Any) -> Any:
    """Normalize a parameter into what Slack expects on the wire.

    Python's urlencode renders True as the string "True", which Slack does not
    accept for its boolean parameters such as ``inclusive`` and
    ``exclude_archived``. This is the single chokepoint every command's request
    passes through, so normalizing here means no command has to remember.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple, set)):
        return ",".join(str(item) for item in value)
    return value


class SlackClient:
    """A bounded, retrying client for the Slack Web API.

    ``transport`` is injectable so the test suite runs entirely offline (D21).
    ``sleep`` and ``jitter`` are attributes rather than imports so retry timing can
    be asserted without the suite actually waiting.
    """

    def __init__(
        self,
        token: str,
        transport: Optional[Callable[..., Response]] = None,
        timeout: float = DEFAULT_TIMEOUT,
        on_wait: Optional[Callable[[str], None]] = None,
    ):
        self._token = token
        self._transport = transport or _urllib_transport
        self.timeout = timeout
        self.sleep = time.sleep
        self.jitter = lambda: random.uniform(0, 0.25)
        # Every wait is announced so a stalled agent run is diagnosable (D17).
        self.on_wait = on_wait or (lambda _message: None)
        self.last_headers: Dict[str, str] = {}

    # -- the guarantee ------------------------------------------------------

    def _check_allowed(self, method: str) -> None:
        if method not in READ_ONLY_METHODS:
            raise ForbiddenMethod(method)

    # -- the request --------------------------------------------------------

    def call(self, method: str, **params: Any) -> Dict[str, Any]:
        """Call one Slack method and return its body, or raise.

        Refuses non-allowlisted methods before any I/O occurs.
        """
        self._check_allowed(method)

        query = {k: _encode_param(v) for k, v in params.items() if v is not None}
        url = BASE_URL + method
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {
            "Authorization": f"Bearer {self._token}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = self._transport(url, headers, self.timeout)
            except TransportError as exc:
                if attempt == MAX_ATTEMPTS:
                    raise SlackApiError(
                        f"could not reach Slack for {method} after "
                        f"{MAX_ATTEMPTS} attempts: {exc}"
                    ) from None
                self._back_off(attempt, f"{method}: {exc}")
                continue
            self.last_headers = Headers(response.headers)

            if response.status == 429:
                self._wait_for_rate_limit(response, method, attempt)
                continue
            if response.status >= 500:
                if attempt == MAX_ATTEMPTS:
                    raise SlackApiError(
                        f"{method} failed: Slack returned HTTP {response.status} "
                        f"after {MAX_ATTEMPTS} attempts"
                    )
                self._back_off(attempt, f"{method}: HTTP {response.status}")
                continue
            if response.status >= 400 and not response.body:
                raise SlackApiError(f"{method} failed: HTTP {response.status}")

            return self._unwrap(method, response)

        raise SlackApiError(f"{method} failed: exhausted {MAX_ATTEMPTS} attempts")

    def _unwrap(self, method: str, response: Response) -> Dict[str, Any]:
        body = response.body
        if body.get("ok"):
            return body

        error = body.get("error", "unknown_error")
        if error in _AUTH_ERRORS:
            detail = f"{method} failed: {error}"
            needed = body.get("needed")
            if needed:
                detail += f" (token is missing the '{needed}' scope)"
            raise AuthError(detail)
        if error in _NOT_FOUND_ERRORS:
            raise NotFound(
                f"{method}: {error}. The conversation does not exist, or this user "
                f"cannot see it."
            )
        raise SlackApiError(f"{method} failed: {error}")

    # -- retry --------------------------------------------------------------

    def _wait_for_rate_limit(self, response: Response, method: str, attempt: int) -> None:
        if attempt == MAX_ATTEMPTS:
            raise SlackApiError(f"{method}: rate limited after {MAX_ATTEMPTS} attempts")
        retry_after = Headers(response.headers).get("Retry-After")
        if retry_after:
            try:
                delay = float(retry_after)
            except ValueError:
                delay = BASE_BACKOFF_SECONDS
            if delay > MAX_RETRY_AFTER_SECONDS:
                raise SlackApiError(
                    f"{method}: Slack asked for a {delay:g}s wait, above the "
                    f"{MAX_RETRY_AFTER_SECONDS:g}s ceiling; try again later"
                )
            if not delay >= 0:  # negative, or NaN, neither of which sleep() accepts
                delay = 0.0
            self.on_wait(f"{method}: rate limited, waiting {delay:g}s as instructed by Slack")
            self.sleep(delay)
        else:
            self._back_off(attempt, f"{method}: rate limited")

    def _back_off(self, attempt: int, reason: str) -> None:
        delay = min(BASE_BACKOFF_SECONDS * (2 ** (attempt - 1)), MAX_BACKOFF_SECONDS)
        delay += self.jitter()
        self.on_wait(f"{reason}, retrying in {delay:.1f}s (attempt {attempt}/{MAX_ATTEMPTS})")
        self.sleep(delay)

    # -- pagination ---------------------------------------------------------

    def paginate(
        self,
        method: str,
        collection: str,
        max_pages: Optional[int] = None,
        **params: Any,
    ) -> Iterator[Page]:
        """Yield successive pages, following Slack's cursor protocol.

        Stops when Slack reports no further cursor, or when ``max_pages`` is
        reached. In the second case the final page still carries its cursor, so a
        caller can report and resume the continuation (D16).
        """
        cursor = params.pop("cursor", None)
        ceiling = MAX_PAGES_PER_CALL if max_pages is None else min(max_pages, MAX_PAGES_PER_CALL)
        pages = 0
        while True:
            body = self.call(method, cursor=cursor or None, **params)
            items = body.get(collection) or []
            next_cursor = (body.get("response_metadata") or {}).get("next_cursor") or ""
            pages += 1
            yield Page(items=items, next_cursor=next_cursor, body=body)

            if not next_cursor:
                return
            if pages >= ceiling:
                if max_pages is None or max_pages > ceiling:
                    self.on_wait(
                        f"{method}: stopped after {ceiling} pages with more available; "
                        f"the result is incomplete"
                    )
                return
            cursor = next_cursor
