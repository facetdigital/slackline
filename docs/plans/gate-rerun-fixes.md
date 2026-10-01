# Plan: Gate Re-run Fixes

## Execution Instructions

When executing this plan:

1. **Work step-by-step.** Complete each step fully before moving to the next.
2. **Test-first within each step.** Write the failing test before the implementation, run
   it, watch it fail for the reason the step describes, then implement until it passes.
   Never edit the test afterwards to make it pass. If a step's test passes before the
   implementation, stop: either the test is wrong or the defect is not what the step says.
3. **Test after each step.** Run the commands under **Test:** and confirm green.
4. **Commit after each step**, using the given commit message. The body says why.
5. **Update documentation continuously.** README, CLAUDE.md, the architecture doc if a
   decision changes, this plan's checkboxes, and the roadmap move in the same PR.
6. **One PR.** Branch `fix/gate-rerun` from `main`, merged with `--no-ff`. Never squash,
   never rebase.
7. **Mark completion.** When every step is done, move this item from "Next Immediate
   Step" to "Completed" in the roadmap and re-run the phase gate; the Read-Only MVP gate
   item closes on a PASS.

Conventions: run the suite as `.venv/bin/python -m pytest -q`; cite decision numbers in
comments; never use an emdash in any text; fixtures stay synthetic; `FakeClient` in
`tests/conftest.py` enforces the read-only allowlist; no runtime dependency (D1).

**Legend:** `Dn` cites [architecture.md](../architecture/architecture.md). `Pn` cites
[product-requirements.md](../requirements/product-requirements.md). `Rerun-N` cites item
N of the fix list in
[phase-gate-read-only-mvp-rerun.md](../assessments/phase-gate-read-only-mvp-rerun.md).

---

## Summary

Close the nine items from the Read-Only MVP gate re-run. Two are medium and are what
keep the gate open: the offline suite passes only because a credential is on disk, and
the bounded collector has no page ceiling. The rest are lows and nits from the same
report: the audit log's symlink policy, the cache root's permissions rule, `--order
newest` under `--threads full`, the fake's model of `conversations.replies`, four
documentation corrections, a shared `page_record()`, and two formatter assertions.

## Requirements

- The default suite passes with no credential in the environment, no `.env` reachable,
  and no Keychain or 1Password subprocess spawned; an argument error exits 2 whether or
  not a credential exists (D18, invariant 10, Rerun-1)
- `collect` under `--all-pages` stops at a page ceiling, reported as its own
  `truncated_by` value with the cursor and a stderr line (D15, D16, Rerun-2)
- A symlinked audit log path is refused with a warning, never followed (D20, Rerun-3)
- The cache root is tightened only when Slackline created it (D7, Rerun-4)
- `--order newest` keeps each root above its replies (D11, D13, Rerun-5)
- The fake models Slack's parent-plus-`limit` reply pages, and `thread` honors its ceiling
  literally (D15, D21, Rerun-6)
- D4b quotes the real scrub pattern; the history-order comment is true; the roadmap's PR 4
  entry records its live results; the set-aside warning is worded by member set on the
  membership-scan path (Rerun-7)
- One `page_record()` builds the page record for both commands (D13, D16, Rerun-8)
- C1 escaping is asserted through every JSON formatter (Rerun-9)

---

## PR: `fix/gate-rerun`

### Step 1: Argument errors exit 2 with no credential anywhere

The contract defect: `cli_read._dispatch` resolves a token before the command parses its
arguments, so an agent with a bad flag and no credential is told "no token" (3) instead
of "bad argument" (2). The test defect: `test_the_cli_records_a_failed_invocation` only
passes because the repo's gitignored `.env` supplies a credential, and on the way the
"offline" suite spawns `security find-generic-password`.

- [x] Write the failing tests first:
  - `tests/conftest.py`: an autouse yield fixture `no_credentials_by_default` that
    `monkeypatch.delenv("SLACK_USER_TOKEN", raising=False)`, replaces
    `slackline.auth.subprocess.run` with a recorder that appends the argv to a list and
    returns a failed `CompletedProcess`-like object (`returncode=1, stdout=""`), and wraps
    `slackline.auth._read_from_dotenv` so a call with `path is None` (the implicit
    `./.env`) is recorded too. After the yield, assert both lists are empty with the
    message "the offline suite must not spawn a credential helper or read ./.env
    (invariant 10)". Tests that legitimately exercise those paths monkeypatch the reader
    functions above this layer (`_read_from_keychain`, `_read_from_op`) or pass an
    explicit `dotenv_path`, as `tests/test_auth.py` already does.
  - `tests/test_cli_argument_errors.py` (new): with `monkeypatch.chdir(tmp_path)` and no
    token, `cli_read.main([...])` returns 2 for each of: `conversation --nonsense`,
    `conversation --since 48h` (no selector), `conversation --channel a --with @b`
    (conflicting selectors), `conversation --channel a --since 48` (bare number),
    `thread --channel a` (no `--ts`), `channels --kind bogus`, `users --bogus`,
    `resolve --bogus`, `resolve` (no selector), `whoami --bogus`. Each with stdout empty
    and stderr starting `slackline-read:`. One more: `conversation --channel a --since 48h`
    with everything valid returns 3 (no credential), proving the order is
    "arguments, then credential".
  - Fix the pre-existing test's environment, not its assertion: in
    `tests/test_audit.py::test_the_cli_records_a_failed_invocation` add
    `monkeypatch.chdir(tmp_path)` so no `.env` is reachable (the autouse fixture already
    removes the variable). Its `assert code == 2` is unchanged.
  Fails: the autouse guard reports a `security` invocation and a `./.env` read from the
  audit test; the new file gets 3 where it asserts 2 on every case that reaches the
  command, and the audit test gets 3.
- [x] Implement, in `src/slackline/commands/_common.py`: `validate_selection(selection)`
  raising the same `ArgumentError`s `Resolver.resolve` raises for no selector and for
  conflicting selector kinds (move the message text here; `Resolver.resolve` keeps its
  checks and calls the same function, so the two cannot drift).
- [x] Implement `parse(argv) -> dict` on every command module: `whoami`, `channels`,
  `users`, `resolve`, `conversation`, `thread`. It does every argument check the command
  makes today and nothing that needs a client: unknown options, missing values, kinds,
  `--threads` and `--order` values, integer flags, `--ts` required, time-window refusal
  in `thread`, `validate_selection`, and the time window itself via `timeparse.window`
  (so an inverted range or a bare number exits 2 before any credential is touched).
  `run(ctx, argv)` calls `parse(argv)` and uses the result; the `-h/--help` handling
  stays in `run` where it is. **As built, after review:** dispatch passes the parsed
  options to `run(ctx, argv, options)` so there is one parse per invocation, and the
  unreachable `--help` blocks in `run` were removed since dispatch handles help before
  `run` is called.
- [x] Implement in `cli_read._dispatch`: after `_load_command` and the help check, call
  `command.parse(rest)` before `resolve_token`. Comment: D18 says 2 means invalid
  arguments, which must not depend on whether a credential is present.
- [x] `CLAUDE.md`: the layout row for `commands/` becomes "each exposing `HELP`,
  `parse(argv)`, and `run(ctx, argv)`; `parse` runs before any credential is resolved";
  delete the carve-out sentence from invariant 10. `docs/plans/development-roadmap.md`:
  delete the CI prerequisite paragraph from the Phase 2.0 CI bullet (it is fixed).
- [x] Verify green, and additionally run the suite from a directory with no `.env`:
  `cd "$(mktemp -d)" && /path/to/repo/.venv/bin/python -m pytest -q /path/to/repo/tests`
  (or `env -i`-style: `SLACK_USER_TOKEN= .venv/bin/python -m pytest -q --rootdir . -p no:cacheprovider tests` after `mv .env .env.off` and back). Both green.

**Satisfies:** D18, P12, P16, CLAUDE.md invariant 10; Rerun-1

**File(s):** `src/slackline/cli_read.py`, `src/slackline/commands/_common.py`, `src/slackline/commands/*.py`, `src/slackline/resolve.py`, `tests/conftest.py`, `tests/test_cli_argument_errors.py`, `tests/test_audit.py`, `CLAUDE.md`, `docs/plans/development-roadmap.md`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_cli_argument_errors.py tests/test_audit.py -q
.venv/bin/python -m pytest -q
```

**Commit message:** `Parse a command's arguments before resolving a credential`

---

### Step 2: A page ceiling for `collect`

- [x] Write the failing tests first: `tests/test_transcript_collect.py`
  - `class EndlessEmptyClient`: every call returns `messages: []` and a fresh cursor
    `f"c{n}"`. `run(client, max_messages=10)` returns with `truncated_by == "max-pages"`,
    `next_cursor` non-empty, `pages == transcript.MAX_PAGES`, and the number of calls is
    exactly `MAX_PAGES`.
  - `class EndlessFilteredClient`: every call returns `limit` `channel_join` messages and
    a fresh cursor. Same assertions; `filtered == MAX_PAGES * limit`.
  - `test_the_ceiling_is_a_named_constant`: `transcript.MAX_PAGES == 100`.
  - `test_a_normal_multi_page_read_is_far_below_the_ceiling`: the three-page fixture
    completes with `truncated_by is None` and `pages == 3`.
  - `test_no_budget_is_still_page_bounded`: `max_messages=None` against the endless
    client stops at `MAX_PAGES`.
  Fails: the loop runs until the test's own guard trips (write the endless clients to
  raise `AssertionError` after `MAX_PAGES * 3` calls so a failing run terminates).
- [x] Implement in `transcript.py`: `MAX_PAGES = 100` with a comment (100 pages at the
  default limit is 20,000 top-level messages per call, far beyond any read this tool is
  for; a server that keeps returning a cursor with nothing behind it is the same hostile or
  broken intermediary D17 capped `Retry-After` for). In the loop, after computing
  `next_cursor`: if `next_cursor` and `pages >= MAX_PAGES`, return with
  `truncated_by="max-pages"` and the cursor. Docstring: the ceiling applies with or without
  a budget.
- [x] Implement the stderr line: `conversation.py` and `thread.py` warn when
  `truncated_by == "max-pages"`: `"{id}: stopped after {MAX_PAGES} pages with more
  available; continue with --cursor from the page record"`. Reply expansion in
  `conversation.py` needs nothing: a thread whose reply fetch hit the ceiling still has a
  cursor and is already counted in `incomplete_threads`.
- [x] Implement in `formatters/text.py` `_page`: `truncated_by == "max-messages"` renders as
  today; any other value renders `f"stopped by the {value} ceiling, there is more"`.
  Test: a page record with `truncated_by: "max-pages"` renders that phrase and the cursor.
- [x] D15: add `MAX_PAGES` to the bounds table with the rationale; D16: `truncated_by` is
  `"max-messages"`, `"max-pages"`, or `null`.
- [x] Verify green.

**Satisfies:** D15, D16, D17 (the same intermediary), P13; Rerun-2

**File(s):** `src/slackline/transcript.py`, `src/slackline/commands/conversation.py`, `src/slackline/commands/thread.py`, `src/slackline/formatters/text.py`, `docs/architecture/architecture.md`, `tests/test_transcript_collect.py`, `tests/test_formatters_text.py`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_transcript_collect.py tests/test_formatters_text.py -q
```

**Commit message:** `Cap the pages one collect call may fetch, and say so when the cap is hit`

---

### Step 3: A symlinked audit log path is refused

- [x] Write the failing test first: `tests/test_audit.py`. Replace
  `test_a_symlinked_log_is_appended_to_but_never_re_permissioned` (this plan changes the
  policy it pinned; say so in the new docstring) with
  `test_a_symlinked_log_is_refused_with_a_warning`: a symlink at the log path pointing at
  a `0o644` file; `record(path=link, warn=warnings.append, ...)` writes nothing to the
  target, leaves it `0o644`, and warns with a message containing `"symlink"`. Add
  `test_a_platform_without_o_nofollow_still_refuses_a_symlink`: `monkeypatch.delattr(os,
  "O_NOFOLLOW", raising=False)`; same assertions (the `is_symlink` check before the open
  is the fallback).
  Fails: the line is appended to the target.
- [x] Implement in `audit.py`: before the open, if `path.is_symlink()`, warn
  `f"refusing to write the audit log through a symlink at {path}"` and return. Add
  `getattr(os, "O_NOFOLLOW", 0)` to the open flags so a link swapped in after the check
  fails the open with `ELOOP`, which the existing handler turns into the usual warning.
  Keep the `fchmod` regular-file gate.
- [x] D20 and the README audit-log paragraph: the path may not be a symlink; a shared or
  world-writable directory is not a place for it.
- [x] Verify green.

**Satisfies:** D20; Rerun-3

**File(s):** `src/slackline/audit.py`, `tests/test_audit.py`, `docs/architecture/architecture.md`, `README.md`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_audit.py -q
```

**Commit message:** `Refuse to write the audit log through a symlink`

---

### Step 4: The cache root follows the same rule as the audit directory

- [x] Write the failing tests first: `tests/test_cache.py`
  - `test_a_cache_root_slackline_did_not_create_is_left_alone`: pre-create `root` at
    `0o755`; `Cache(root=..., warn=warnings.append).put("users", {})`; mode still `0o755`
    and a warning containing `"0700"` was emitted once.
  - `test_a_cache_root_slackline_created_is_owner_only`: the existing
    `test_cache_directory_is_created_with_restrictive_permissions` covers this; keep it.
  - `test_no_warning_for_an_owner_only_root`: pre-create at `0o700`; no warning.
  - `test_warn_is_optional`: no `warn` argument, looser root, no exception.
  Fails: the root is chmodded to `0o700` and there is no `warn` parameter.
- [x] Implement in `cache.py`: `Cache.__init__` gains `warn: Optional[Callable[[str],
  None]] = None`. `_ensure_root`: create grandparents with `mkdir(parents=True,
  exist_ok=True)`, then `os.mkdir(root, 0o700)`; on success `chmod 0o700`; on
  `FileExistsError` check `stat(root).st_mode & 0o077` and, if any group or other bit is
  set, warn once per process: `f"cache directory {root} is not owner-only (mode {mode:o});
  it names colleagues; consider chmod 700"`. Comment cites `audit.py`'s rule (D7, D20).
  `cli_read.Context.directory()` passes `warn=self.warn`.
- [x] D7: the directory is created `0700`; one Slackline did not create is left alone and
  a looser mode is reported.
- [x] Verify green.

**Satisfies:** D7; Rerun-4

**File(s):** `src/slackline/cache.py`, `src/slackline/cli_read.py`, `tests/test_cache.py`, `docs/architecture/architecture.md`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_cache.py -q
```

**Commit message:** `Tighten the cache root only when Slackline created it`

---

### Step 5: `--order newest` keeps each root above its replies

- [x] Write the failing test first: `tests/test_cmd_conversation.py`
  - `test_newest_first_keeps_replies_beneath_their_root`: history
    `[plain_text, thread_parent]` (plain_text is newer than the thread root? check the
    fixture `ts` values and pick whichever is older as the "other" message), replies
    `[thread_parent, thread_reply]`, `--threads full --order newest`. The emitted order
    is: the newer top-level group first, and within the thread's group the root
    immediately followed by its reply. Assert the index of `thread_reply` is exactly one
    after `thread_parent`.
  Fails: the reply precedes the root.
- [x] Implement in `conversation.py`: build `groups: List[List[record]]`, one per
  top-level message (root plus its inlined replies); `--order newest` reverses the list
  of groups; flatten to `records`. Fix the comment at the sort: "Slack returns history in
  either order depending on the window; sort ascending so replies land under the right
  root" (Rerun-7's comment item lands here).
- [x] Verify green.

**Satisfies:** D11, D13, D14, P4; Rerun-5, part of Rerun-7

**File(s):** `src/slackline/commands/conversation.py`, `tests/test_cmd_conversation.py`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_cmd_conversation.py -q
```

**Commit message:** `Keep replies beneath their root under --order newest`

---

### Step 6: The fake models parent-plus-limit replies; `thread` honors its ceiling literally

Observed live on 2026-08-30: `conversations.replies` returns the parent in addition to
`limit` replies on the first page, and only replies on cursor pages.

- [x] Write the failing tests first:
  - `tests/test_fake_client.py`: `test_replies_pages_carry_the_parent_in_addition_to_limit`:
    a `conversations.replies` payload of `[parent, r1, r2, r3]` with `limit=2` and no cursor
    returns `[parent, r1, r2]` and a cursor; the continuation with that cursor and
    `limit=2` returns `[r3]` (no parent). `test_history_pages_are_not_affected`: the same
    shape under `conversations.history` returns exactly `limit` items.
  - `tests/test_cmd_thread.py`: `test_the_ceiling_is_literal_on_the_first_page`:
    `replies(*THREE)` with `--max-messages 2` returns exactly 2 messages and the
    `conversations.replies` call carried `limit=1`. The existing
    `test_a_long_thread_truncates_at_the_ceiling_and_still_exits_0` must keep passing
    unmodified.
  Fails: the fake serves exactly `limit`, and the command requests `limit=2`, so the
  count assertion passes but the `limit=1` assertion fails; once the fake is fixed, the
  count becomes 3 until the command is fixed.
- [x] Implement in `tests/conftest.py` `_page`: when the method is
  `conversations.replies`, the cursor is empty, and a limit is given, serve
  `items[:1] + items[1:1 + limit]`.
- [x] Implement in `transcript.collect`: keyword `first_page_reserve: int = 0`; on the
  first request only (no cursor), `page_limit = max(1, page_limit - first_page_reserve)`.
  `thread.py` passes `first_page_reserve=1` for its main read and the redirect read (not
  the one-message probe). Comment: Slack counts the parent outside `limit`.
- [x] `thread` `HELP` and D15: the ceiling counts the root.
- [x] Verify green.

**Satisfies:** D15, D21; Rerun-6

**File(s):** `tests/conftest.py`, `tests/test_fake_client.py`, `src/slackline/transcript.py`, `src/slackline/commands/thread.py`, `tests/test_cmd_thread.py`, `docs/architecture/architecture.md`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_fake_client.py tests/test_cmd_thread.py -q
```

**Commit message:** `Model Slack's parent-plus-limit reply pages and keep the thread ceiling literal`

---

### Step 7: One `page_record()` for both commands

- [x] Write the failing tests first: `tests/test_transcript_collect.py` (or a new
  `tests/test_transcript_page.py`): `page_record(conversation_id, returned, filtered,
  next_cursor, truncated_by, incomplete_threads=0, thread_ts=None)` returns a dict with
  `type: "page"` and exactly the keys `conversation_id, returned, filtered,
  incomplete_threads, has_more, next_cursor, truncated_by`, plus `thread_ts` only when
  given; `has_more == bool(next_cursor)`. `tests/test_cmd_thread.py`:
  `test_the_thread_page_record_has_the_shared_shape`: the `page` record carries
  `incomplete_threads == 0`.
  Fails: no such function; the thread page lacks `incomplete_threads`.
- [x] Implement `page_record` in `transcript.py`; both commands emit it. D16's sample and
  text note the `thread_ts` extra for `thread`.
- [x] Verify green.

**Satisfies:** D13, D16; Rerun-8

**File(s):** `src/slackline/transcript.py`, `src/slackline/commands/conversation.py`, `src/slackline/commands/thread.py`, `tests/test_transcript_collect.py`, `tests/test_cmd_thread.py`, `docs/architecture/architecture.md`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_transcript_collect.py tests/test_cmd_thread.py tests/test_cmd_conversation.py -q
```

**Commit message:** `Build the page record in one place for both transcript commands`

---

### Step 8: Formatter assertions and the set-aside wording

- [x] Write the failing tests first: `tests/test_formatters_text.py`:
  `test_json_and_raw_escape_c1_too`: `format_json` and `format_raw` of a record containing
  `"\x9b"` emit `\u009b` and no raw byte. `tests/test_resolve.py`:
  `test_the_set_aside_warning_on_the_scan_path_is_worded_by_members`: on the fallback
  path (`@joan @jamien` with `MEMBERS["G399"]` set to `["U001","U002","U007"]` via
  monkeypatch so an archived row confirms beside the live G301), the warning says
  `"with these members"` and not `"matching name"`.
  Fails: the first passes already (it pins; say so in its docstring and confirm by
  reasoning that `_dumps` is shared); the second fails on wording.
- [x] Implement: `_confirm` takes a `by: str` argument, `"name"` or `"members"`, and the
  warning reads `f"archived group DM{plural} with {'a matching name' if by == 'name' else
  'these members'} ..."`. The candidates pass calls it with `"name"`, the fallback with
  `"members"`.
- [x] Verify green.

**Satisfies:** P8, P11, D8; Rerun-9, part of Rerun-7

**File(s):** `src/slackline/resolve.py`, `tests/test_resolve.py`, `tests/test_formatters_text.py`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_resolve.py tests/test_formatters_text.py -q
```

**Commit message:** `Word the archived set-aside warning by how the candidates matched`

---

### Step 9: Documentation corrections and close-out

- [x] Test-first: n/a, documentation. Verification is the grep below.
- [x] `docs/architecture/architecture.md` D4b: replace the quoted pattern with the real one
  from `errors.py:29` and one sentence on why it is wide (URL-safe characters, so a
  partial redaction cannot leave the tail of a secret).
- [x] `docs/plans/development-roadmap.md`: in the Completed entry for "Gate fixes PR 4",
  add the measured live results from PR #13's description (continuation contiguous with
  no gap and no overlap on a 32-message window; thread continuation exact with the
  parent-plus-limit observation; reply-ts redirect announced; 753 conversations with 305
  archived hidden by default; audit log `0600`). Remove the two wire-fact follow-ups that
  Steps 5 and 6 closed.
- [x] Every checkbox in this plan ticked; the roadmap moves this item to Completed with
  the date and the PR number at merge.
- [x] Verify: `grep -n "xox\[abposr\]" docs/architecture/architecture.md` returns nothing;
  `grep -n "newest-first" src/slackline/commands/conversation.py` returns nothing;
  `.venv/bin/python -m pytest -q` green; the no-`.env` run from Step 1 green.

**Satisfies:** D4b; Rerun-7; roadmap "Docs move with the code"

**File(s):** `docs/architecture/architecture.md`, `docs/plans/development-roadmap.md`, `docs/plans/gate-rerun-fixes.md`

**Test:**
```bash
grep -n "xox\[abposr\]" docs/architecture/architecture.md; grep -n "newest-first" src/slackline/commands/conversation.py
.venv/bin/python -m pytest -q
```

**Commit message:** `Quote the real scrub pattern and record the live results the roadmap lacked`

---

## Files Modified (Summary)

| File | Steps |
|------|-------|
| `tests/conftest.py` | 1, 6 |
| `tests/test_cli_argument_errors.py` | 1 |
| `tests/test_audit.py` | 1, 3 |
| `src/slackline/cli_read.py` | 1, 4 |
| `src/slackline/commands/_common.py` | 1 |
| `src/slackline/commands/whoami.py`, `channels.py`, `users.py`, `resolve.py` | 1 |
| `src/slackline/commands/conversation.py` | 1, 2, 5, 7 |
| `src/slackline/commands/thread.py` | 1, 2, 6, 7 |
| `src/slackline/resolve.py` | 1, 8 |
| `src/slackline/transcript.py` | 2, 6, 7 |
| `tests/test_transcript_collect.py` | 2, 7 |
| `src/slackline/formatters/text.py` | 2 |
| `tests/test_formatters_text.py` | 2, 8 |
| `src/slackline/audit.py` | 3 |
| `src/slackline/cache.py` | 4 |
| `tests/test_cache.py` | 4 |
| `tests/test_cmd_conversation.py` | 5 |
| `tests/test_fake_client.py` | 6 |
| `tests/test_cmd_thread.py` | 6, 7 |
| `tests/test_resolve.py` | 8 |
| `CLAUDE.md` | 1 |
| `README.md` | 3 |
| `docs/architecture/architecture.md` | 2, 3, 4, 6, 7, 9 |
| `docs/plans/development-roadmap.md` | 1, 9 |
