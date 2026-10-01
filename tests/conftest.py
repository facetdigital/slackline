"""Shared test scaffolding. Nothing here touches the network (D21)."""
from __future__ import annotations

import pytest

from slackline import api


class FakeClient:
    """A SlackClient stand-in driven by a method-to-payload map.

    Enforces the same allowlist as the real client so a test cannot accidentally
    assert behavior that the shipped guarantee would forbid.

    Pages the way Slack does. A payload may be one page (a dict) or a list of
    pages. ``call()`` serves at most ``limit`` items and returns a cursor that
    resumes exactly where it stopped, mid-page included, so a test can observe a
    continuation that skips or repeats messages. Slack's own cursors are opaque;
    the fake's mid-page cursor is ``fake:<page>:<offset>`` and the page-boundary
    cursor is whatever the fixture page declares in ``response_metadata``.
    """

    def __init__(self, responses=None, headers=None):
        self.responses = dict(responses or {})
        self.calls = []
        self.last_headers = dict(headers or {})

    def call(self, method, **params):
        if method not in api.READ_ONLY_METHODS:
            from slackline.errors import ForbiddenMethod

            raise ForbiddenMethod(method)
        self.calls.append((method, params))
        payload = self.responses.get(method)
        if payload is None:
            raise AssertionError(f"FakeClient has no canned response for {method}")
        if callable(payload):
            # The callable already saw the cursor and chose its page; serve
            # that page from offset 0 rather than trying to locate it again.
            result = payload(**params)
            if isinstance(result, Exception):
                raise result
            return self._page([result], None, params.get("limit"), method)
        if isinstance(payload, Exception):
            raise payload
        pages = payload if isinstance(payload, list) else [payload]
        return self._page(pages, params.get("cursor"), params.get("limit"), method)

    @staticmethod
    def _locate(pages, cursor):
        """Which page, and how far into it, a cursor refers to."""
        if not cursor:
            return 0, 0
        if cursor.startswith("fake:"):
            try:
                _, page_index, offset = cursor.split(":")
                page_index, offset = int(page_index), int(offset)
            except ValueError:
                raise AssertionError(f"FakeClient cannot parse cursor {cursor!r}") from None
            assert 0 <= page_index < len(pages), f"FakeClient has no page {page_index}"
            return page_index, offset
        for index in range(1, len(pages)):
            previous = pages[index - 1]
            if (previous.get("response_metadata") or {}).get("next_cursor") == cursor:
                return index, 0
        raise AssertionError(f"FakeClient has no page for cursor {cursor!r}")

    def _page(self, pages, cursor, limit, method=None):
        page_index, offset = self._locate(pages, cursor)
        page = pages[page_index]
        lists = [key for key, value in page.items() if isinstance(value, list)]
        if not lists:
            return page
        assert len(lists) == 1, (
            f"FakeClient cannot tell which of {lists} is the collection; "
            f"keep fixture pages to one list"
        )
        collection = lists[0]
        everything = page[collection]
        items = everything[offset:]
        if limit is not None:
            if method == "conversations.replies" and not cursor and items:
                # Observed live: Slack returns the parent in addition to
                # `limit` replies on the first page, and only replies after.
                items = items[:1] + items[1:1 + limit]
            else:
                items = items[:limit]
        if offset + len(items) < len(everything):
            next_cursor = f"fake:{page_index}:{offset + len(items)}"
        else:
            next_cursor = (page.get("response_metadata") or {}).get("next_cursor") or ""
        return dict(page, **{collection: items}, response_metadata={"next_cursor": next_cursor})

    def paginate(self, method, collection, max_pages=None, **params):
        pages = self.responses.get(method)
        if not isinstance(pages, list):
            pages = [pages]
        count = 0
        # Slack honors exclude_archived on users.conversations; so does the fake,
        # or a test could not tell "fetched and hidden" from "never fetched".
        hide_archived = str(params.get("exclude_archived", "")).lower() == "true"
        for body in pages:
            if callable(body):
                body = body(**params)
            if isinstance(body, Exception):
                # The real client raises from inside pagination too; a fake that
                # could only fail on call() would let a test assert an error
                # path that the shipped code does not actually have.
                raise body
            self.calls.append((method, params))
            count += 1
            cursor = (body.get("response_metadata") or {}).get("next_cursor") or ""
            items = body.get(collection) or []
            if hide_archived and method == "users.conversations":
                items = [c for c in items if not c.get("is_archived")]
            yield api.Page(items=items, next_cursor=cursor, body=body)
            if not cursor:
                return
            if max_pages is not None and count >= max_pages:
                return


@pytest.fixture(autouse=True)
def no_credentials_by_default(monkeypatch):
    """The offline suite needs no secret and spawns no credential helper (invariant 10).

    The variable is removed for every test; a test that needs a token sets one.
    A Keychain or 1Password subprocess, or a read of the implicit ./.env, is
    recorded and fails the test afterwards, so a test that passes only because a
    developer machine has a credential on it cannot exist unnoticed. Tests of the
    credential chain itself monkeypatch the reader functions above this layer or
    pass an explicit dotenv path, as tests/test_auth.py does.

    In-process only: a test that drives the CLI in a child process and reaches
    the credential step would escape this guard. Today every child-process test
    exits before that step (--version, --help, argument errors).
    """
    import pathlib
    import subprocess
    import types

    from slackline import auth

    # All three credential variables, so a developer who configured the
    # documented 1Password source does not get a false failure from the op
    # reader being reached.
    for var in ("SLACK_USER_TOKEN", "SLACKLINE_OP_REF", "SLACKLINE_OP_ACCOUNT"):
        monkeypatch.delenv(var, raising=False)
    spawned, dotenv_reads = [], []

    def recording_run(argv, *args, **kwargs):
        spawned.append(list(argv))
        return types.SimpleNamespace(returncode=1, stdout="", stderr="")

    real_dotenv = auth._read_from_dotenv

    def guarded_dotenv(path=None):
        # The implicit ./.env is a violation only when it exists: then a
        # credential would actually be read. An empty directory is fine.
        if path is None:
            if pathlib.Path(".env").exists():
                dotenv_reads.append(str(pathlib.Path(".env").resolve()))
            return None
        return real_dotenv(path)

    # A shim in place of auth's subprocess reference, never the global module:
    # tests elsewhere drive the CLI through a real subprocess.
    shim = types.SimpleNamespace(run=recording_run, SubprocessError=subprocess.SubprocessError)
    monkeypatch.setattr(auth, "subprocess", shim)
    monkeypatch.setattr(auth, "_read_from_dotenv", guarded_dotenv)
    yield
    assert not spawned and not dotenv_reads, (
        "the offline suite must not spawn a credential helper or read ./.env "
        f"(invariant 10): spawned={spawned} dotenv={dotenv_reads}"
    )


@pytest.fixture(autouse=True)
def no_audit_log_by_default(monkeypatch):
    """Keep the offline suite from writing to the real state directory.

    Running the tests must not leave a record in ~/.local/state that looks like
    real usage, and a subprocess test invoking the CLI would otherwise do
    exactly that. Tests that exercise the log set the variable themselves;
    monkeypatch.setenv in the test overrides this, and the value propagates to
    subprocesses, which inherit the environment.
    """
    monkeypatch.setenv("SLACKLINE_AUDIT_LOG", "none")


@pytest.fixture
def fake_client():
    return FakeClient
