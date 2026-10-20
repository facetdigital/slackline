# Slackline: guidance for agents working on this codebase

Read this before changing anything. Most of it is invariants: things that are true on
purpose, that a reasonable-looking change would break, and that have a test standing
behind them.

## What this is

A suite of small, single-purpose CLI executables that read from Slack, built so an AI agent
can use them as reliably as a human can. Not an MCP server, not a bot, not a daemon.
`slackline-read` is the only binary in v0.2.

- Product intent: [docs/requirements/product-requirements.md](docs/requirements/product-requirements.md)
- Numbered design decisions D1..D22: [docs/architecture/architecture.md](docs/architecture/architecture.md)
- What is built and what is next: [docs/plans/development-roadmap.md](docs/plans/development-roadmap.md)

Code comments and commit messages cite those decision numbers. Keep doing that. A comment
saying `(D16)` is worth more than a paragraph re-deriving the reasoning.

## Invariants

**1. `slackline-read` cannot write to Slack.** Every request passes an allowlist of Slack
methods in `api.py`. `tests/test_readonly_guard.py` fails if a write method is added to it.
Do not widen that set to make something convenient work. If a feature seems to need a write
method, it belongs in `slackline-write`, which does not exist yet and is gated behind a
phase gate on purpose.

**2. No token reaches output, ever.** `errors.scrub()` redacts token-shaped strings and is
applied to every error message, to unexpected tracebacks, and to each audit-log line before
it is written. There is no `--token` flag, deliberately: `argv` is world-readable via `ps`
and lands in shell history. Do not add one.

**3. A truncated read exits 0.** It succeeded; it just did not finish. Continuation lives
in the trailing `page` record. Non-zero would make every long conversation look like a
failure to `set -e` and to agent harnesses. Exit code 6 is deliberately unassigned so
"partial results" cannot creep back in as a meaning.

**4. Nothing is silently dropped.** `render.py` handles blocks, attachments, files, and
unknown block types, and reports a message it cannot render rather than skipping it. The
same reasoning one layer up: a record type with no registered renderer gets a labelled dump
from the text formatter, because "no renderer yet" must not be indistinguishable from "no
results".

**5. Filtering is always counted and reported.** Anything dropped by `--exclude-bots`, by
the system-message filter, or by a ceiling appears in the `page` record. A subset must
never look like a quiet conversation.

**6. Ambiguity is a hard failure.** Never resolved by guessing (D8). Group DMs are matched
on member set, never on name, because Slack's group DM names are neither unique nor kept
current. `resolve` is the one place a caller inspects candidates and picks. The single
carve-out is an archived namesake of a live conversation: the live one wins, the archived
ones are announced on stderr, and two live matches are still a tie.

**7. stdout carries only requested data.** Diagnostics, warnings, and usage go to stderr.
Commands never write to stdout themselves; they call `ctx.emit()` and a formatter renders
once. That is what keeps D19 a property of the architecture rather than of each author
remembering it.

**8. Zero runtime dependencies.** Adding one is an architecture decision that needs a new
numbered entry in the architecture doc, not a convenience. `urllib` is sufficient.

**9. The audit log never records message content.** It records what was read, not what it
said. A log carrying content would be a second unmanaged copy of the workspace in a
dotfile.

**10. The offline suite touches no network and needs no secret.** Fixtures only. Live
checks are opt-in, live-only, and separate, under `tests/live`. They are excluded from the
default run by `addopts` in `pyproject.toml`. An autouse fixture in `tests/conftest.py`
removes `SLACK_USER_TOKEN` and fails any test that spawns a credential helper or reads
`./.env`, so a test cannot pass only because a developer machine holds a credential.

## Versioning

One version for the whole thing: the binary and the skill are released together and
share it. It is declared in four places, `VERSION` (the number of record),
`pyproject.toml`, `src/slackline/__init__.py`, and the skill frontmatter's `version:`,
and a test keeps the four equal, so a bump touches all four in one commit. The README
quick start installs the release wheel by a URL that names the version; a second test
keeps that URL equal to `VERSION`, so a bump updates it too. Releases are
tagged `vX.Y.Z` with the wheel, the source tarball, and a skill-only zip attached
(steps in CONTRIBUTING.md). Installed artifacts are updated
independently (a binary via `uv tool install --force --reinstall`, skill copies via re-copy), so
installed versions can drift from each other; that is accepted, deliberately. Do not
build machinery to reconcile them.

## Testing rules

Run the suite with the virtualenv's interpreter, `.venv/bin/python -m pytest -q`, or
activate the virtualenv first. A bare `python3` on macOS is the system or Xcode
interpreter and has no `pytest`; bare `python` is usually absent entirely. The live checks
under `tests/live` are excluded from that run by `addopts` in `pyproject.toml` and are
opt-in via `SLACKLINE_LIVE_TEST=1`.

Tests are written expecting **correct** behavior. A new test must fail against the broken
code and pass against the fix, and is never edited afterward to make it pass. A test that
passes against the bug proves nothing.

When adding a test, verify it actually fails first. Check against `HEAD`, not against a
staged copy: `git checkout HEAD -- <file>` reverts to the commit, `git checkout -- <file>`
reverts to the index, and if the fix is already staged the second one will hand you a
false pass.

Fixtures must not contain real message content or real colleague names. Everything in
`tests/fixtures/` is synthetic and stays that way.

A manual test step handed to a person must have been run once, against real state, by
whoever wrote it. Two steps in the v0.2.0 manual test plan failed for the plan's own
reasons: a "no token" check found one anyway, because the Keychain item an earlier step
added still answered the credential chain; and `grep -c` was used to count records in a
cache file that is one line of JSON. A "nothing to fall back on" test must remove every
source in `auth.SOURCE_ORDER`, not just the environment variable.

## Layout

| File | Holds |
|------|-------|
| `cli_read.py` | Global flags, `Context`, dispatch, exit-code mapping, audit wrapping. No Slack logic |
| `api.py` | HTTP, retries, rate limiting, pagination, the read-only allowlist |
| `auth.py` | The credential chain, environment variable first; an explicit `--token-file` overrides it |
| `errors.py` | Exception hierarchy, exit codes, token scrubbing |
| `cache.py`, `directory.py` | The cached user and conversation index |
| `resolve.py` | Selector to conversation IDs, member-set verified |
| `timeparse.py` | Durations, dates, and windows |
| `render.py` | One Slack message to readable text |
| `transcript.py` | The record shape and the bounded fetch loop (`collect`) shared by every transcript-producing command |
| `formatters/` | Records to stdout, one format per function |
| `commands/` | One module per command, each exposing `HELP`, `parse(argv)`, and `run(ctx, argv, options)`; `parse` does every argument check and runs before any credential is resolved, and `options` is what it returned |

Shared behavior goes in a shared module. `_common.py` exists because `resolve`,
`conversation`, and `thread` must not drift into accepting different spellings of the same
selector; `transcript.py` exists because `conversation` and `thread` must not drift into
two JSONL contracts. Prefer extracting to duplicating.

## Workflow

One feature, one branch, one PR. Full-history merges: `--no-ff`, never squash, never
rebase. Docs move with the code in the same PR, including the plan's checkboxes. Do not
leave them stale; a plan file that does not track reality costs a later session an hour of
re-derivation to answer "where are we".

Commit messages explain **why**, not what the diff already shows. No AI attribution or
co-author trailers.
