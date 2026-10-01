# Phase Gate: Read-Only MVP (third run)

Date: 2026-08-30
Auditor: Phase gate review
Diff range: 9711f4a..e3a7651f496a4da02ebf16f512121ec433127fd5
Verdict: PASS

## Summary

The second gate returned PASS_WITH_FINDINGS on 2026-08-30 with two mediums: the offline
suite was green only because a gitignored `.env` supplied a credential, and
`transcript.collect` looped without limit under `--all-pages` against a server that kept
returning a cursor. PR #14 executed the nine-step plan `gate-rerun-fixes.md` against that
gate's nine-item fix list. This run verified by reproduction, not by checkbox, that both
mediums are closed: the suite passes 488/488 from an empty directory with no credential
anywhere, a planted test that would pass only via the developer's `.env` or Keychain is
failed by the conftest guard, and every server-driven loop (`collect`, reply expansion,
`api.paginate`) stops at 100 pages with the cursor reported. All nine plan steps are
satisfied and all nine fix-list items are closed. The allowlist guard, token scrubbing,
and every D18 exit code were re-proven the hard way. What remains is three lows and five
nits, none of which touches a promise the phase made, so the verdict is PASS and the
Read-Only MVP gate item can close.

One remark on history, since the caller asked: `0cfaccc` ("Complete: gate re-run fixes")
was committed directly on main before PR #14 merged, and its content was wrong as well as
premature: it moved the item into "Completed" while leaving its status "Ready to
implement" and displaced the PHASE GATE item. It was reverted in `d164d85`, the PR merged,
and `e3a7651` re-applied the change correctly. The net roadmap is right. The plan file
`fff10b6` was also committed straight to main rather than on the branch. Neither needs a
fix; both are worth knowing when reading `git log --first-parent`.

## Test truthfulness

Suite results, all with `-p no:cacheprovider` except the first:

- Repo root, `.venv/bin/python -m pytest -q`: 488 passed in 0.52s.
- Empty directory (`cd "$(mktemp -d)"`, absolute paths to the interpreter and `tests`,
  `--ignore=tests/live`): 488 passed. No `.env` reachable, no variable set.
- `SLACKLINE_OP_REF=op://x/y/z` set: 488 passed. The autouse guard deletes the variable,
  so the `op` reader is never reached.
- `SLACK_USER_TOKEN=xoxp-fake` set: 488 passed. Same guard, same reason.

Test count rose from 434 to 488 (+54). No `xfail`, no `pending`, no marker skips outside
`tests/live` (3 opt-in skips with a stated reason, file unchanged in the range). No TODO,
FIXME, HACK, or XXX in `src`, `tests`, or the docs. No emdash added anywhere in the diff
range (the only ones in the tree remain in the pre-existing `initial-idea.md`). Coverage is
still not measured in this repository.

Pre-existing tests touched in the range, each judged from `git diff 9711f4a..HEAD -- tests/`:

- `test_audit.py::test_the_cli_records_a_failed_invocation`: `monkeypatch.chdir(tmp_path)`
  added; the `assert code == 2` is unchanged. Setup only. Legitimate: the assertion now
  holds for the right reason.
- `test_auth.py::clean_env`: stubs `_read_from_keychain` above the subprocess layer.
  Setup only, and required by the guard; the chain tests still walk env, file, op, dotenv.
- `test_audit.py::test_a_symlinked_log_is_appended_to_but_never_re_permissioned` replaced
  by `test_a_symlinked_log_is_refused_with_a_warning`. This is a deliberate policy
  reversal the second gate asked for (fix item 3); the new docstring says so. Not a
  weakening: the new test asserts the target is untouched and unchanged in mode.
- `test_fake_client.py::test_a_single_dict_page_is_also_sliced` switched its method from
  `conversations.replies` to `conversations.history`, because reply pages are no longer
  plain slices by design (Step 6). The property it pins, single-dict slicing, is intact.
- `test_review_regressions.py`: two stub command classes gained a `parse` staticmethod to
  match the new module shape. Shape change, no assertion touched.

The roadmap gate item's five specifics, each verified rather than assumed:

- **The allowlist guard fails when a write method is introduced.** A temporary
  `src/slackline/_gate_probe.py` containing `client.call("chat.postMessage", ...)` made
  `test_every_slack_method_in_the_package_is_read_only` fail with
  `src/slackline/_gate_probe.py:2 uses 'chat.postMessage', which is not read-only`
  (1 failed, 7 passed). The probe was deleted; `git status --porcelain` was empty.
  `READ_ONLY_METHODS` is byte-identical to 9711f4a.
- **The token-scrubbing tests fail when scrubbing is disabled.** A pytest plugin object
  passed to `pytest.main(plugins=[...])` replaced `errors.scrub` with `str` and
  `_TOKEN_RE` with a never-matching pattern, in-process, no files edited. Result: 10
  failed, 478 passed. The failures are exactly the scrubbing tests: both audit-log scrub
  tests, all four cases of `test_tokens_are_scrubbed_from_error_messages`, the URL/JSON
  case, and all three URL-safe-character cases. Scrubbing is load-bearing.
- **No test passes by asserting broken behavior.** Every changed test file was read.
  `test_a_ceiling_of_one_yields_the_root_and_one_reply` pins a documented exception
  (`--max-messages 1` returns two messages because a page of zero cannot be requested);
  the HELP text and D15 both state it, so it is a conscious pin, not a defect asserted as
  correct. `test_json_and_raw_escape_c1_too` says in its docstring that it passes today
  by construction, which the plan required it to say. Nothing else is questionable.
- **No real message content or colleague name entered the repository via fixtures.**
  `git diff --stat 9711f4a..HEAD -- tests/fixtures` is empty: neither fixture file
  changed. New test data in the range is synthetic (`U001`, `G399`, `msg(n)`, `T1`).
  The first gate's low on first-name handles and workspace identifiers is unchanged and
  remains recorded under Phase 3.1.
- **Exit codes match D18 in practice, and a truncated read exits 0.** Driven through
  `cli_read.main()` with `api._urllib_transport` replaced by a canned transport, from an
  empty directory, Keychain reader stubbed, no network: 2 (bad flag, no credential; no
  selector, no credential), 3 (valid arguments and no credential, proving the order is
  arguments then credential; `invalid_auth`), 4 (two channels named `eng`), 5 (unknown
  name; `channel_not_found`), 7 (a command coerced into `chat.postMessage`), 130
  (`KeyboardInterrupt`), 1 (unexpected exception, with `xoxp-secret-...` rendered as
  `xox***`). stdout was empty on every failure path. `--max-messages 5` over 12 messages
  exited 0 with `returned: 5, has_more: true, next_cursor: "c5", truncated_by:
  "max-messages"`. An endless-cursor history under `--all-pages` exited 0 after exactly
  100 requests with `truncated_by: "max-pages"`, `next_cursor: "c100"`, and the stderr
  line naming `--cursor`.

The two mediums from the second gate, closed with evidence:

- **The offline suite depends on no credential.** Beyond the four green runs above, a
  temporary `tests/_gate_probe_cred.py` was written whose body calls
  `cli_read.main(["whoami"])` from the repo root (where the gitignored `.env` lives) with
  the transport stubbed to fail, asserting `code in (1, 3)` so the body passes either
  way. The guard failed it at teardown:
  `spawned=[['security', 'find-generic-password', '-s', 'slackline', '-w']]
  dotenv=['/Users/.../slackline/.env']` (1 passed, 1 error). The guard records the
  Keychain spawn and the `.env` read without performing either: the shim returns
  `returncode=1` and the guarded reader returns `None`. The probe was deleted; the tree
  was clean. The guard is in-process only, as its docstring says; the three child-process
  tests in `test_package.py` and the `run_cli` tests in `test_review_regressions.py` all
  exit on `--version`, `--help`, or a global-flag error before the credential step, and
  `run_cli` also pops the variable and runs from `/tmp`. The `.env` was not read by this
  gate.
- **`collect` and `api.paginate` are bounded.** Reproduced with fakes: empty pages with a
  fresh cursor forever, budget 500, `all_pages=True`: 100 calls, `max-pages`, cursor
  `c100`. Full pages of `channel_join` forever: 100 calls, `filtered=20000`. No budget
  (the reply-expansion shape): 100 calls. Without `all_pages`: 1 call, `truncated_by`
  None, cursor reported. `api.paginate` on `users.list` with an endless transport: 100
  calls, last page still carries its cursor, `on_wait` received `users.list: stopped
  after 100 pages with more available; the result is incomplete`. `max_pages=3` stops at
  3 with no warning; `max_pages=1000` stops at 100 and warns. With `transcript.MAX_PAGES`
  set to `10**9` in-process, the four ceiling tests in `test_transcript_collect.py` fail
  with `collect did not stop` at the file's fixed 300-call guard, so they are
  load-bearing.

Ways the green suite could still mislead, found by doing:

- The three ceiling tests outside `test_transcript_collect.py`
  (`test_the_page_ceiling_is_announced_for_a_conversation`, `..._for_a_thread`,
  `test_paginate_stops_at_a_default_page_ceiling_and_announces_it`) size their loop
  guard as `3 * <the constant under test>`. With the ceiling removed they spin rather than
  fail; a full-suite run with both constants neutered had to be killed at 120s. They do
  not lie (a hang is not a pass), but they do not fail cleanly either. Nit, below.
- `test_every_command_exposes_the_help_text_dispatch_relies_on` checks `HELP` only, while
  dispatch now also calls `parse`. A future command module without `parse` would reach
  production as an `AttributeError` (exit 1). Nit, below.

## Citation walk

| Plan step | Satisfies | Status | Notes |
|---|---|---|---|
| 1 Argument errors exit 2 with no credential anywhere | D18, P12, P16, invariant 10; Rerun-1 | satisfied | `cli_read.py:340-352` calls `command.parse(rest)` before `resolve_token`; every module in `commands/` exposes `parse` and `run(ctx, argv, options)`; `_common.validate_selection` wraps `resolve.check_selectors` so the two messages cannot drift (built in `resolve.py` rather than `_common.py` as the plan said, same effect); `tests/test_cli_argument_errors.py` covers 13 cases plus the exit-3 ordering proof; conftest guard verified above; CLAUDE.md invariant 10 carve-out removed and the layout row updated; roadmap CI paragraph removed |
| 2 A page ceiling for `collect` | D15, D16, D17, P13; Rerun-2 | satisfied | `transcript.py:34` `MAX_PAGES = 100`; check at `:202-204` after the budget check, before the `all_pages` return; both commands warn with `--cursor` guidance; `formatters/text.py:172-175` renders `max-pages` without a leading `--`; D15 table row and D16 prose added; reproduced through `main()` above |
| 3 A symlinked audit log path is refused | D20; Rerun-3 | satisfied | `audit.py:109-112` refuses before the open; `O_NOFOLLOW` at `:120-121` closes the swap window; two tests (with and without `O_NOFOLLOW`); D20 and README amended |
| 4 Cache root follows the audit directory's rule | D7; Rerun-4 | satisfied | `cache.py:72-95`: mkdir decides "created"; a pre-existing root is left alone and a looser mode warns once per `Cache`; `cli_read.py:131` passes `warn=self.warn`; five tests including the wiring; D7 amended |
| 5 `--order newest` keeps replies beneath their root | D11, D13, D14, P4; Rerun-5, part of Rerun-7 | satisfied | `conversation.py:231-290` builds groups and reverses groups, not records; the "newest-first" comment replaced with the observed either-order statement; `test_newest_first_keeps_replies_beneath_their_root` asserts adjacency without assuming which root is newer |
| 6 Fake models parent-plus-limit replies; `thread` ceiling literal | D15, D21; Rerun-6 | satisfied | `conftest.py:82-85` serves `items[:1] + items[1:1+limit]` on the first reply page only; `transcript.collect` gains `first_page_reserve`; `thread.py:183, 243` pass 1 on the main and redirect reads, not the one-message probe; the ceiling-of-1 exception documented in HELP and D15; `test_a_long_thread_truncates_at_the_ceiling_and_still_exits_0` unmodified and green |
| 7 One `page_record()` for both commands | D13, D16; Rerun-8 | satisfied | `transcript.py:89-117`; both commands emit it; `thread` now carries `incomplete_threads: 0`; shape pinned by `test_the_thread_page_record_has_the_shared_shape` and three unit tests; D16 names the function and the `thread_ts` extra. Landed in the same commit as Step 6 (`6d4ba8a`), which the PR description discloses |
| 8 Formatter assertions and set-aside wording | P8, P11, D8; Rerun-9, part of Rerun-7 | satisfied | `resolve.py:153-180` `_confirm(..., by=)`, callers pass `"name"` and `"members"`; both wordings pinned in `test_resolve.py`; `test_json_and_raw_escape_c1_too` covers `format_json` and `format_raw` |
| 9 Documentation corrections and close-out | D4b; Rerun-7; roadmap "Docs move with the code" | satisfied | D4b quotes `(?:xox[a-z]\|xapp)-[A-Za-z0-9%/+=._-]+` matching `errors.py:29`; `grep xox\[abposr\]` returns nothing; `grep newest-first conversation.py` returns nothing; PR 4 entry carries the measured live results; every checkbox ticked; roadmap entry moved to Completed with date and PR number |

## Second gate's fix list

| Item | Status | Evidence |
|---|---|---|
| 1 Offline suite passes with no credential; arguments before credential; guard against `security` and `./.env`; carve-out removed | closed | Four green environments; planted-test probe failed by the guard; `cli_read.py:345` ordering; CLAUDE.md invariant 10 rewritten |
| 2 Bound `collect` under `--all-pages`, distinct `truncated_by`, stderr line, applied to reply expansion | closed | `MAX_PAGES`, `"max-pages"`, warnings in both commands; reply expansion goes through the same `collect` and stopped at 100 in the no-budget reproduction; `api.paginate` bounded too (security pass, beyond the ask) |
| 3 Audit log symlink policy decided and tested | closed | Refusal with warning; `O_NOFOLLOW`; README constraint on the directory; old test replaced with the docstring explaining why |
| 4 `cache._ensure_root` tightens only what it created | closed | `cache.py:72-95`; five tests |
| 5 `--order newest` under `--threads full` | closed | Groups reversed; adjacency test |
| 6 Fake models parent-plus-limit; `thread` requests N-1 or documents | closed | Both halves done: the fake models it and `thread` reserves one; documented in HELP and D15 |
| 7 D4b pattern; "newest-first" comment; PR 4 live results; set-aside wording | closed | All four verified by grep and by reading |
| 8 `page_record()` with `incomplete_threads` for both; D16 notes `thread_ts` | closed | `transcript.page_record`; D16 amended |
| 9 C1 assertion through `format_json` and `format_raw` | closed | `test_json_and_raw_escape_c1_too` |

## Drift findings

- **low** | `src/slackline/cache.py:79-81, 127` | `_ensure_root` runs outside the `try` that degrades a failed write to a warning, so a `SLACKLINE_CACHE_DIR` whose parent is unwritable, whose parent is a regular file, or which is a dangling symlink raises `PermissionError`, `FileExistsError`, or `FileNotFoundError` out of `put()`. Through `main()` that is exit 1 "unexpected failure: [Errno 13] Permission denied", contradicting the module's own rule at line 148 ("a cache that cannot be written is a slow run, not a failed one") and D7's intent. Pre-existing before the range (the old `mkdir` raised the same way) but the range added the degrade path around only the write, leaving the root creation outside it | Move `_ensure_root()` inside the guarded region (or wrap it in its own `try/except OSError` that warns and returns), and add one test per failure mode asserting the warning and no exception
- **low** | `src/slackline/directory.py:63-79, 151-166`; `src/slackline/api.py:346-352` | When `api.paginate` stops at `MAX_PAGES_PER_CALL`, the directory loops do not look at the final page's `next_cursor`; the incomplete index is written to the cache for 24 hours (users) or 1 hour (conversations). The warning fires only on the run that built it; every later run within the TTL resolves selectors against a silently partial directory, which is the case the comment at `api.py:37-40` says must never be mistaken for a complete one. Only reachable past 20,000 rows or with a broken intermediary, hence low | In `directory.py`, do not `cache.put` an index whose last page still carried a cursor (or store an `incomplete` flag and warn on every load); test with a paginate fake that stops at the ceiling
- **low** | `docs/plans/development-roadmap.md` Phase 2.0 "Follow-ups recorded by the gate-fix review passes" | The bullet still lists as open work three things PR #14 closed: the `page_record()` helper, the page ceiling for `collect` under `--all-pages`, and the FIFO at the audit path (now `O_NONBLOCK`, tested). The "optional bound on reply expansion under `--all-pages`" is also now bounded by `MAX_PAGES` per thread, which D15 documents. Only the bidi-override item remains genuinely open. A later session would plan work that is already done | Trim the bullet to the bidi item (and, if wanted, a note that reply expansion is bounded per thread but not in aggregate)
- **nit** | `src/slackline/transcript.py:127` | `Collected.truncated_by` is annotated `# "max-messages", or None`; the range added `"max-pages"` | Update the comment
- **nit** | `tests/test_cmd_conversation.py:480`, `tests/test_cmd_thread.py:537`, `tests/test_api.py:122` | The endless-cursor guards are `3 * transcript.MAX_PAGES` and `api.MAX_PAGES_PER_CALL * 3`, so if the ceiling is removed the tests hang instead of failing (observed: a neutered run had to be killed) | Use a fixed literal guard as `test_transcript_collect.py` does with `GUARD = 300`
- **nit** | `README.md` "Reading the output" | The section explains `--max-messages` truncation and the cursor but not the `max-pages` stop, which an agent will see as `stopped by the max-pages ceiling, there is more` in text output | One sentence after the truncation paragraph
- **nit** | `tests/test_review_regressions.py:83-89` | The command-shape test checks `HELP` only; dispatch now requires `parse` and `run(ctx, argv, options)` as well | Extend the test to assert `callable(module.parse)` and the `run` signature
- **nit** | `src/slackline/audit.py:131` | In a directory another account can write, that account can hardlink the user's own log (allowed on macOS and on Linux without `protected_hardlinks`), driving `st_nlink` to 2 so Slackline refuses its own file thereafter. A warning per run, no data exposure, and the README already says to keep the log in a directory only the user can write | No code change needed; the README constraint is the fix. Recorded so the next person reading the `nlink == 1` check knows the trade

Scope leakage: none found. Every change in the range traces to a plan step or to a
review or security pass named in a commit message (`ce25a82`, `c9ab40e`, `1dfdb2d`). The
extra work those passes added (three credential variables guarded, cache symlink refusal
on read and write, `api.paginate` ceiling, FIFO and hardlink refusal) is each reflected in
code, tests, and D7 or D20 in the same PR. Zero runtime dependencies holds
(`dependencies = []`). No `--token` flag. The allowlist is unchanged. Architecture
decisions D4a, D4b, D7, D8, D15, D16, D17, D18, D20, D21 as amended all match the code
they describe; nothing in the range widened a boundary or blurred a layer. `cli_read.py`
still holds no Slack logic; commands still emit through `ctx.emit()` only.

## Security

Triage: hit. PR #14 touched credential ordering (`cli_read.py`, `commands/*.py`,
`_common.py`), file creation and permissions (`audit.py`, `cache.py`), server-controlled
loop bounds (`transcript.py`, `api.py`), and the test-side credential guard
(`tests/conftest.py`). Full adversarial review performed on all of those.

No critical, high, or medium security findings.

- **Read-only allowlist: not weakened.** `READ_ONLY_METHODS` (`api.py:49-61`) is
  byte-identical to 9711f4a; the planted-violation probe proves the guard fires; the
  coerced `chat.postMessage` through `main()` exited 7 before any transport call. The new
  `first_page_reserve` only lowers a `limit` that already went through `urlencode` at the
  single chokepoint (`api.py:230-233`).
- **Credential ordering (`cli_read.py:340-352`).** `command.parse` runs before
  `resolve_token`. Nothing in any `parse` reaches a client, a file, or a subprocess: the
  only I/O-adjacent step is `timeparse.window`, and a hostile `--timezone` such as
  `../../../etc/passwd` exits 2 with "unknown timezone" (verified), so the reordering
  opens no path from argv to the filesystem before authentication. Global-flag errors
  (`--timeout`, `--format`, `--token-source`) still exit 2. The token still travels only in
  the `Authorization` header (`api.py:235`).
- **Token hygiene: holds.** Scrubbing shown load-bearing (10 failures when disabled). The
  new warning strings in `audit.py`, `cache.py`, `api.py`, and both commands interpolate
  only paths the user configured, method names, and integers; none carries Slack content
  or a token. `_clean(scrub(...))` ordering in `cli_read.py:368, 391` unchanged.
- **Audit log (`audit.py:94-153`).** Directory created by `os.mkdir` deciding ownership,
  unchanged. The symlink check at `:109` plus `O_NOFOLLOW` at `:121` close the
  check-then-open window: a link swapped in after the check fails the open with `ELOOP`
  and lands in the warning. `O_NONBLOCK` turns a planted FIFO with no reader into an
  immediate `ENXIO` (tested with a 3s alarm); a FIFO with a reader opens, is rejected by
  `S_ISREG`, and is closed. The `fstat` gate at `:129-136` refuses anything that is not a
  regular file with one link owned by this uid, all decided on the descriptor, so no
  TOCTOU between the gate and the `fchmod` or the write. A device node, a hardlink to a
  stranger's file, or a file another account pre-created is refused with a warning and
  the line is dropped rather than delivered; D20 says so. `O_NONBLOCK` remains set on the
  descriptor for a regular file, which is harmless on macOS and Linux. Grandparents are
  still created at umask; only the immediate parent is `0700`, matching D20's wording.
  The hardlink-denial nit above is the only residual, and it is a warning, not a leak.
- **Cache (`cache.py:72-157`).** Root creation decided by `os.mkdir`; a pre-existing root
  is never chmodded and a looser mode is reported once, with the warning naming the
  actual risk (another writer decides what a name resolves to). `get` refuses a symlink
  at the file's name, so a link to a poisoned document in a shared root is a miss, not a
  resolution. `put` refuses a symlink at the final name, unlinks whatever sits at the
  predictable temp name, then opens it `O_CREAT|O_EXCL|O_NOFOLLOW` at `0600`, so a link
  raced in between the unlink and the open fails with `EEXIST` rather than being
  followed; a directory planted there degrades to a warning (tested). `os.replace` over a
  stranger's regular file in a sticky directory fails with `EPERM` and warns. Residual,
  documented and accepted: a regular file another account plants at `users-<team>.json`
  in a user-supplied shared root is read without an ownership check; the mode warning is
  the mitigation. The `_ensure_root` exceptions that escape the warn path are the low
  above (availability only, no exposure). `team_id` validation from PR #12 is unchanged.
- **Server-controlled loop bounds.** `collect` stops at `MAX_PAGES` with or without a
  budget and with or without `all_pages`; the budget check precedes it so the more
  specific cause is reported. Reply expansion (`conversation.py:262-273`) runs through
  the same function with `max_messages=None`, so each thread is bounded at 100 pages;
  the aggregate is `max_messages` threads times 100 pages, which D15 now states.
  `api.paginate` is bounded at `MAX_PAGES_PER_CALL` regardless of `max_pages`, announced
  through `on_wait` (which is `ctx.warn`, so `--quiet` silences it, consistent with every
  other diagnostic). The resolver's membership scan keeps `MAX_MEMBER_PROBES = 60` and a
  truncated member list can only fail a match, never confirm one. `Retry-After` handling
  (`api.py:292-311`) is unchanged from PR #12.
- **Test-side guard (`tests/conftest.py:123-175`).** Replaces `auth.subprocess` with a
  shim carrying `run` and `SubprocessError`, never the global module, so the suite's own
  child-process tests still run. The shim records and returns failure; the guarded
  `_read_from_dotenv` records only when `./.env` actually exists and returns `None`
  without reading it, so a developer's real token is never loaded into the test process.
  The three credential variables are deleted for every test. Verified by the planted
  probe above. The in-process limitation is documented and currently moot.
- **Subprocesses** (`auth.py:58-63, 73-78`): unchanged, fixed argv lists, no shell,
  timeouts. **`.env` in the working directory** remains the last credential source, as
  the first gate noted; this gate did not read it.

Earlier security findings, status: first gate's lows on `Retry-After`, audit permissions,
control characters, and `team_id` were closed in PRs #12 and #13 (verified by the second
gate and unchanged here). The second gate's lows on the audit symlink follow and the
unconditional cache chmod are closed in this range (items 3 and 4 above). The independent
security review on PR #14 itself (`1dfdb2d`) raised the FIFO block, the hardlink and
foreign-owner write, the cache symlink read, and the unbounded directory loops; all four
are closed in the same PR with tests. The `.env`-in-cwd low is unchanged and accepted.

## Fix list (seeds the next planning session)

1. `cache.py`: bring `_ensure_root()` under the same degrade-to-warning rule as the
   write, so an unwritable, file-shadowed, or dangling-symlink `SLACKLINE_CACHE_DIR`
   warns and continues instead of exiting 1; one test per failure mode, written first.
2. `directory.py`: do not cache an index whose final page still carried a cursor after
   `api.paginate` stopped at its ceiling, or store an `incomplete` flag and warn on every
   load within the TTL; test with a paginate fake that stops at `MAX_PAGES_PER_CALL`.
3. Roadmap Phase 2.0 follow-ups bullet: remove the `page_record()`, `collect` ceiling,
   and FIFO items that PR #14 closed, and note that reply expansion is now bounded per
   thread; leave the bidi-override item.
4. `transcript.py:127`: update the `Collected.truncated_by` comment to include
   `"max-pages"`.
5. Replace the `3 * <constant>` loop guards in `test_cmd_conversation.py`,
   `test_cmd_thread.py`, and `test_api.py` with a fixed literal so a removed ceiling
   fails the test rather than hanging it.
6. README "Reading the output": one sentence on the `max-pages` stop and its stderr line.
7. Extend `test_every_command_exposes_the_help_text_dispatch_relies_on` to require
   `parse` and the three-argument `run`.
