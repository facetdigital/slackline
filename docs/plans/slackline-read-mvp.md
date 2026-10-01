# Plan: `slackline-read` MVP

## Execution Instructions

1. **Work step-by-step.** Complete each step fully before the next.
2. **Test-first within each step.** Write the failing test, watch it fail, then implement
   until it passes. Never edit the test to make it pass.
3. **Commit after each step**, using the given message.
4. **Group steps into PRs** as marked. One PR per group, reviewed before merge.
5. **Update docs continuously**: README, architecture doc if a decision changes, and this
   plan's checkboxes.

**On the `python3 -m pytest` commands below:** they assume the project virtualenv is
active. Without it, run `.venv/bin/python -m pytest` instead. A bare `python3` on macOS is
the system or Xcode interpreter and has no `pytest`.

**Legend:** `Dn` cites [architecture.md](../architecture/architecture.md). `Pn` and `Rn`
cite [product-requirements.md](../requirements/product-requirements.md).

---

## Summary

Build `slackline-read`: a zero-dependency, strictly read-only Slack CLI with six commands,
usable headlessly by an AI agent with nothing but `SLACK_USER_TOKEN` in its environment.

## Requirements

- Six commands: `whoami`, `channels`, `users`, `resolve`, `conversation`, `thread`
- Read-only enforced by an allowlist, guarded by a test (D3)
- Environment-variable-first credentials, never requiring TouchID (D4a, P16)
- Renderer that never silently drops a message (D9, P8)
- Group DM resolution verified against member sets (D6)
- Ambiguity is a hard failure everywhere (D8, P10)
- Bounded reads, continuation in the payload, exit 0 on success (D15, D16)
- Offline fixture suite (D21)

---

## PR 1: Foundation documents

### Step 1: Requirements, architecture, roadmap, and this plan

- [x] Test-first: n/a, documentation only
- [x] Write `docs/requirements/product-requirements.md`
- [x] Write `docs/architecture/architecture.md` with numbered decisions D1..D22
- [x] Write `docs/plans/development-roadmap.md` with a phase gate after the MVP
- [x] Write this plan

**Satisfies:** roadmap Phase 1.0 entry criteria

**Commit message:** `Add product requirements, architecture, and MVP plan`

---

## PR 2: Core plumbing and the first command

### Step 2: Package skeleton and test harness

- [x] Test-first: `tests/test_package.py` asserts `slackline.__version__` exists and that
      `python3 -m slackline.cli_read --version` exits 0. Fails: no package.
- [x] Create `pyproject.toml` with `dependencies = []`, `requires-python = ">=3.9"`, and
      the `slackline-read` console script
- [x] Create `src/slackline/__init__.py` and a `cli_read.py` handling only `--version`
      and `--help`
- [x] Verify green

**Satisfies:** D1, D2

**File(s):** `pyproject.toml`, `src/slackline/__init__.py`, `src/slackline/cli_read.py`, `tests/test_package.py`

**Test:** `python3 -m pytest tests/test_package.py -q`

**Commit message:** `Add package skeleton with zero-dependency pyproject and CLI entry point`

### Step 3: Error hierarchy, exit codes, and token scrubbing

- [x] Test-first: `tests/test_errors.py` asserts each exception maps to its D18 exit code,
      that 6 is unassigned, and that `SlacklineError("failed with xoxp-abc123...")`
      formats with the token replaced by `xox***`. Fails: no module.
- [x] Implement `src/slackline/errors.py`: base `SlacklineError` carrying `exit_code`,
      subclasses for auth (3), args (2), ambiguity (4), not-found (5), forbidden-method
      (7), API (1)
- [x] Implement `scrub()` over `xox[abposr]-[A-Za-z0-9-]+`, applied in `__str__`
- [x] Verify green

**Satisfies:** D18, D4b, P14

**File(s):** `src/slackline/errors.py`, `tests/test_errors.py`

**Test:** `python3 -m pytest tests/test_errors.py -q`

**Commit message:** `Add error hierarchy with exit code mapping and token scrubbing`

### Step 4: HTTP layer with the read-only method allowlist

- [x] Test-first: `tests/test_api.py` asserts a call to `chat.postMessage` raises
      `ForbiddenMethod` (exit 7) **without opening a socket**, that an allowlisted method
      builds the expected request, that `ok: false` becomes a `SlackApiError`, and that
      `Retry-After` is honored with a patched sleep. Fails: no module.
- [x] Implement `src/slackline/api.py`: `READ_ONLY_METHODS` frozenset, `SlackClient.call()`
      checking the allowlist before any I/O, `urllib` request building, JSON decode,
      429/5xx retry with `Retry-After` and capped jittered backoff (D17)
- [x] Implement `paginate()` yielding pages and surfacing `next_cursor`
- [x] Verify green

**Satisfies:** D3, D17, R1

**File(s):** `src/slackline/api.py`, `tests/test_api.py`

**Test:** `python3 -m pytest tests/test_api.py -q`

**Commit message:** `Add HTTP layer with enforced read-only method allowlist and retry policy`

### Step 5: The allowlist guard test

- [x] Test-first: `tests/test_readonly_guard.py` greps every `.py` file in the package for
      Slack method-shaped string literals (`word.word`, matched against a known Slack
      method vocabulary) and asserts every one is in `READ_ONLY_METHODS`. Prove it works
      by temporarily introducing `chat.postMessage` and confirming the test fails.
- [x] Implement nothing. This step exists to make the guarantee testable.
- [x] Verify green after removing the temporary violation

**Satisfies:** D3, R1

**File(s):** `tests/test_readonly_guard.py`

**Test:** `python3 -m pytest tests/test_readonly_guard.py -q`

**Commit message:** `Add guard test preventing the read-only allowlist from being widened`

### Step 6: Credential resolution chain

- [x] Test-first: `tests/test_auth.py` asserts env beats file beats dotenv, that a missing
      token raises auth error exit 3, that `--token-source env` with no env var fails
      immediately rather than falling through, and that no code path reads `sys.argv` for
      a `--token` value. Fails: no module.
- [x] Implement `src/slackline/auth.py` with the five-source chain and `--token-source`
- [x] Keychain and `op` sources shell out and are skipped silently when unavailable
- [x] Verify green

**Satisfies:** D4a, P16, P17, P18

**File(s):** `src/slackline/auth.py`, `tests/test_auth.py`

**Test:** `python3 -m pytest tests/test_auth.py -q`

**Commit message:** `Add credential resolution chain with environment variable precedence`

### Step 7: `whoami`

- [x] Test-first: `tests/test_cmd_whoami.py` runs the command against a stubbed client and
      asserts text and JSON output contain user, team, and granted scopes, and that
      nothing but the payload reaches stdout. Fails: no command.
- [x] Implement `src/slackline/commands/whoami.py` over `auth.test`, reading granted scopes
      from the `X-OAuth-Scopes` response header
- [x] Wire dispatch and global flags into `cli_read.py`
- [x] Verify green

**Satisfies:** PRD 9.1, D19

**File(s):** `src/slackline/commands/whoami.py`, `src/slackline/cli_read.py`, `tests/test_cmd_whoami.py`

**Test:** `python3 -m pytest -q && python3 -m slackline.cli_read whoami`

**Commit message:** `Add whoami command reporting identity, workspace, and granted scopes`

---

## PR 3: Directory cache and inventory commands

### Step 8: Cache layer

- [x] Test-first: `tests/test_cache.py` asserts a written cache reads back, that an expired
      entry is treated as a miss, that writes are atomic (no partial file after a simulated
      crash mid-write), that team IDs partition the cache, and that message content is
      rejected by the cache API. Fails: no module.
- [x] Implement `src/slackline/cache.py`: TTL, atomic `os.replace`, team-scoped filenames,
      `--refresh-cache` support
- [x] Verify green

**Satisfies:** D7

**File(s):** `src/slackline/cache.py`, `tests/test_cache.py`

**Test:** `python3 -m pytest tests/test_cache.py -q`

**Commit message:** `Add TTL cache with atomic writes for user and conversation directories`

### Step 9: Directory building, `users`, and `channels`

- [x] Test-first: `tests/test_directory.py` asserts the user index maps ID, handle, and
      display name; that group DM names parse into member handles on the `--` separator;
      and that a name shared by two conversation IDs is retained as two entries rather
      than collapsed. Uses fixtures reproducing the observed collision. Fails: no module.
- [x] Implement directory building over `users.list` and `users.conversations` (D5)
- [x] Implement `commands/users.py` and `commands/channels.py` with `--kind` filtering and
      `--match` substring search
- [x] Verify green

**Satisfies:** D5, D6, D7, PRD 9.2

**File(s):** `src/slackline/directory.py`, `src/slackline/commands/users.py`, `src/slackline/commands/channels.py`, `tests/test_directory.py`

**Test:** `python3 -m pytest -q && python3 -m slackline.cli_read channels --kind public`

**Commit message:** `Add directory building with users and channels inventory commands`

---

## PR 4: The renderer

### Step 10: mrkdwn resolution

- [x] Test-first: `tests/test_render_mrkdwn.py` covers every row of the D10 table, plus:
      unresolvable user ID falls back to `@Uxxxx`, entity unescaping happens after markup
      parsing so a literal `&lt;@U1&gt;` is not misparsed as a mention, and nested and
      malformed markup does not raise. Fails: no module.
- [x] Implement `render.py:resolve_mrkdwn()`
- [x] Verify green

**Satisfies:** D10, P8

**File(s):** `src/slackline/render.py`, `tests/test_render_mrkdwn.py`

**Test:** `python3 -m pytest tests/test_render_mrkdwn.py -q`

**Commit message:** `Add mrkdwn resolution turning Slack markup into readable text`

### Step 11: Whole-message rendering from all content sources

- [x] Test-first: `tests/test_render_message.py` asserts a message with empty `text` and a
      `rich_text` block renders its content; that attachments, files, and each observed
      subtype render; and critically that **no fixture message renders as an empty
      string**, since 18% of real messages have no `text` and silent loss is the failure
      this whole tool exists to prevent. Fails: no implementation.
- [x] Implement recursive block flattening, attachment and file rendering, and the
      `[no renderable content: subtype=X]` fallback
- [x] Build `tests/fixtures/messages.json` reproducing every observed shape with
      synthesized content (D21)
- [x] Verify green

**Satisfies:** D9, P8, PRD section 7

**File(s):** `src/slackline/render.py`, `tests/fixtures/messages.json`, `tests/test_render_message.py`

**Test:** `python3 -m pytest tests/test_render_message.py -q`

**Commit message:** `Render messages from blocks, attachments, and files so none are dropped`

---

## PR 5: Selector resolution

### Step 12: Time window parsing

- [x] Test-first: `tests/test_timeparse.py` covers relative durations, bare dates as local
      midnight, ISO with offset, `--between`, `--after`/`--before`, timezone override, and
      that an inverted range is an argument error (exit 2). Fails: no module.
- [x] Implement `src/slackline/timeparse.py` producing Slack epoch bounds
- [x] Verify green

**Satisfies:** D14, P3

**File(s):** `src/slackline/timeparse.py`, `tests/test_timeparse.py`

**Test:** `python3 -m pytest tests/test_timeparse.py -q`

**Commit message:** `Add time window parsing producing Slack epoch bounds`

### Step 13: The resolver and `resolve`

- [x] Test-first: `tests/test_resolve.py` asserts an unambiguous handle resolves; that two
      users matching one handle exits 4 with both candidates on stderr and nothing on
      stdout; that a group DM whose name matches two conversation IDs is disambiguated by
      member set via `conversations.info`; that a genuine two-way tie exits 4; and that
      `conversations.open` is never called. Fails: no module.
- [x] Implement the four-stage resolver from D6
- [x] Implement `commands/resolve.py`
- [x] Verify green

**Satisfies:** D6, D8, P10, PRD 9.3

**File(s):** `src/slackline/resolve.py`, `src/slackline/commands/resolve.py`, `tests/test_resolve.py`

**Test:** `python3 -m pytest tests/test_resolve.py -q`

**Commit message:** `Add member-set-verified resolver with ambiguity as a hard failure`

---

## PR 6: Retrieval and output

### Step 14: Formatters

- [x] Test-first: `tests/test_formatters.py` asserts the text transcript matches an
      expected rendering; that every JSONL line is independently valid JSON carrying both
      raw `ts` and ISO `time`; that the trailing `page` record appears; and that no
      diagnostic text reaches stdout in either format. Fails: no module.
- [x] Implement `formatters/text.py`, `formatters/jsonl.py`, `formatters/json.py`,
      `formatters/raw.py`. As built: jsonl, json, and raw are functions in
      `formatters/__init__.py` beside the registry; only `text.py` is its own module.
- [x] Verify green

**Satisfies:** D12, D13, D19, P6, P7, P9

**File(s):** `src/slackline/formatters/*.py`, `tests/test_formatters.py`

**Test:** `python3 -m pytest -q`. Note: the assertions landed across the command tests
(`test_cmd_whoami.py`, `test_cmd_inventory.py`, `test_cmd_conversation.py`,
`test_review_regressions.py`) rather than in a single `test_formatters.py`, which was
never created.

**Commit message:** `Add text, JSONL, JSON, and raw formatters with stream discipline`

### Step 15: `conversation`

- [x] Test-first: `tests/test_cmd_conversation.py` asserts a windowed read returns messages
      in oldest-first order; that `--max-messages` truncation **exits 0** with
      `truncated_by` in the trailing record; that `--threads full` inlines replies; that
      `channel_join` is excluded by default and restored by `--include-system`; and that
      the filtered count is reported. Fails: no command.
- [x] Implement `commands/conversation.py`: selector plus window to paginated history,
      thread expansion, filtering, ceilings, continuation
- [x] Verify green

**Satisfies:** D11, D15, D16, P1..P5, PRD 9.4, 9.5

**File(s):** `src/slackline/commands/conversation.py`, `tests/test_cmd_conversation.py`

**Test:** `python3 -m pytest tests/test_cmd_conversation.py -q`

**Commit message:** `Add conversation command with windowed, thread-aware, bounded reads`

### Step 16: `thread`

- [x] Test-first: `tests/test_cmd_thread.py` asserts a root plus replies renders as one
      coherent thread and that a `ts` naming no thread exits 5. Fails: no command.
- [x] Implement `commands/thread.py` over `conversations.replies`
- [x] Verify green

**Satisfies:** D13, P4

**File(s):** `src/slackline/commands/thread.py`, `tests/test_cmd_thread.py`

**Test:** `python3 -m pytest tests/test_cmd_thread.py -q`

**Commit message:** `Add thread command retrieving a root message and its replies`

### Step 17: Audit log

- [x] Test-first: `tests/test_audit.py` asserts one line per invocation with the expected
      fields, that **no message content appears**, that a token in argv is scrubbed, that
      `none` disables it, and that an unwritable path warns without failing the command.
      Fails: no module.
- [x] Implement `src/slackline/audit.py`, wired into `cli_read.py`
- [x] Verify green

**Satisfies:** D20

**File(s):** `src/slackline/audit.py`, `tests/test_audit.py`

**Test:** `python3 -m pytest tests/test_audit.py -q`

**Commit message:** `Add local audit log recording invocations without message content`

---

## PR 7: Documentation and live verification

### Step 18: Live smoke test, opt-in

- [x] Test-first: `tests/live/test_smoke.py`, skipped unless `SLACKLINE_LIVE_TEST=1` and a
      token are both present. Read-only calls only.
- [x] Verify it skips cleanly with no token, and passes with one
- [x] Verify green

**Satisfies:** D22

**File(s):** `tests/live/test_smoke.py`

**Test:** `python3 -m pytest tests/live -q` and `SLACKLINE_LIVE_TEST=1 python3 -m pytest tests/live -q`

**Commit message:** `Add opt-in live smoke test for wire format drift`

### Step 19: README and installation

- [x] Test-first: n/a, documentation
- [x] Write `README.md`: what it is, the read-only guarantee, install, credentials for both
      agents and humans, every command with examples, exit codes, the agent recipe
- [x] Write `CLAUDE.md` with the invariants a future agent must not break
- [x] Verify every command example in the README actually runs

**Satisfies:** PRD section 9

**File(s):** `README.md`, `CLAUDE.md`

**Test:** run every README example against the live workspace, read-only

**Commit message:** `Add README and contributor guidance`

### Step 20: End-to-end verification against the live workspace

- [x] Install via `uv tool install .` and confirm `slackline-read` is on PATH
- [x] Walk all six PRD section 9 success criteria against the real workspace, read-only
- [x] Confirm the audit log recorded the session with no message content
- [x] Update the roadmap: move Phase 1.0 to Completed

**Satisfies:** PRD section 9, roadmap Phase 1.0 definition of done

**Test:** manual, recorded in the PR description

**Commit message:** `Verify MVP against live workspace and mark Phase 1.0 complete`

---

## Files Modified (Summary)

| File | Steps |
|------|-------|
| `pyproject.toml` | 2 |
| `src/slackline/cli_read.py` | 2, 7, 17 |
| `src/slackline/errors.py` | 3 |
| `src/slackline/api.py` | 4 |
| `src/slackline/auth.py` | 6 |
| `src/slackline/cache.py` | 8 |
| `src/slackline/directory.py` | 9 |
| `src/slackline/render.py` | 10, 11 |
| `src/slackline/timeparse.py` | 12 |
| `src/slackline/resolve.py` | 13 |
| `src/slackline/formatters/*` | 14 |
| `src/slackline/commands/*` | 7, 9, 13, 15, 16 |
| `src/slackline/audit.py` | 17 |
| `README.md`, `CLAUDE.md` | 19 |
