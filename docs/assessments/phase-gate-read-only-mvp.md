# Phase Gate: Read-Only MVP

Date: 2026-08-28
Auditor: Phase gate review
Diff range: f489261..e20c3ca (repository init through PR #9; the phase is PRs #1 to #9 on main)
Verdict: PASS_WITH_FINDINGS

## Summary

Phase 1.0 promised a zero-dependency, strictly read-only `slackline-read` with six
commands, a renderer that never silently drops a message, member-set-verified group DM
resolution, bounded reads with continuation in the payload, and an offline fixture suite.
It delivered all of that. The read-only guarantee is real: the allowlist is enforced
before any I/O in one chokepoint, and the guard test was shown during this gate to fail
against a planted write method and against a widened allowlist. Token scrubbing was shown
to be load-bearing the same way: disabling it fails seven tests. Every D18 exit code was
exercised through `main()` with a fake transport and matched, and a truncated read exits 0.

The verdict is not PASS because one promise is only partly delivered. D16 says
continuation lives in the trailing `page` record; in practice, after a `--max-messages`
truncation the cursor points at the page after the one that was cut, so a `--cursor`
continuation skips the rest of the truncated page, and a truncation on the final page
reports `has_more: true` with no cursor at all. The default configuration never hits this
(one page of 200 against a ceiling of 500), the page-boundary case works and was verified
live, and a caller can work around it with `--all-pages` or a narrower window, which is why
this is a high finding rather than a FAIL. It must be fixed before Phase 2.0 builds
multi-conversation reads on top of the same loop.

## Test truthfulness

`.venv/bin/python -m pytest -q`: 315 passed in 0.48s. `tests/live`: 2 skipped, by the
intended `skipif` on `SLACKLINE_LIVE_TEST`, with no token present. Nothing else is
skipped, pending, or xfail. No TODO, FIXME, HACK, or XXX markers anywhere in `src`,
`tests`, or the docs.

Roadmap gate items, verified rather than assumed:

- **The allowlist test fails when a write method is introduced.** A temporary
  `src/slackline/_gate_probe.py` containing `client.call("chat.postMessage")` made
  `test_every_slack_method_in_the_package_is_read_only` fail with the file and line
  named. The probe was removed; the tree is clean. Widening `READ_ONLY_METHODS` in-process
  with `chat.postMessage` fails two further tests.
- **The scrubbing test fails when scrubbing is removed.** Replacing `errors.scrub` with the
  identity function in-process fails all four parametrized cases of
  `test_tokens_are_scrubbed_from_error_messages`, the URL/JSON case, and both audit-log
  scrubbing tests.
- **No test passes by asserting broken behavior.** Every test file was read. The one test
  that had passed against its bug (`test_main_flushes_stdout_inside_the_guarded_region`)
  was rewritten to assert the actual defect and says so in its docstring.
- **Fixtures.** Message content is synthetic throughout; every URL and email is on
  `example.com`. See the low finding on identifiers below.
- **Exit codes.** 2, 3, 4, 5, 7 each produced through `main()`; 0 on truncation; stdout
  empty on every failure path.

Ways the green suite can mislead, found by reading:

- No test continues a read from a truncated page and checks that nothing is skipped.
  `test_a_cursor_is_preserved_for_continuation` only checks that a cursor is passed
  through. This is the gap that let the high finding through.
- `FakeClient.paginate` ignores request parameters, so no test can notice that
  `exclude_archived="true"` is always sent and `--include-archived` cannot work.
- No test covers a `thread_broadcast` message under `--threads full`, or a thread whose
  replies exceed one `conversations.replies` page.
- Multi-conversation reads are tested at the resolver but not at the command.
- `test_every_command_exposes_the_help_text_dispatch_relies_on` skips any command that
  fails to import, which would make it vacuous if a module went missing. All six exist
  today, so it currently checks what it claims.
- Coverage is not measured in this repository, and the phase started from zero code, so
  "coverage dropped versus phase start" does not apply.

## Citation walk

| Plan step | Satisfies | Status | Notes |
|---|---|---|---|
| 1 Foundation docs | roadmap 1.0 entry | satisfied | |
| 2 Skeleton | D1, D2 | satisfied | `dependencies = []`; one console script; `--version` and `--help` exit 0 |
| 3 Errors and scrubbing | D18, D4b, P14 | satisfied | Regex widened past the plan's `xox[abposr]` to cover xoxc/xoxd/xapp and URL-safe characters; 6 unassigned and tested |
| 4 HTTP and allowlist | D3, D17, R1 | satisfied | Refusal precedes I/O; `Retry-After` honored but uncapped (low) |
| 5 Guard test | D3, R1 | satisfied | AST scan rather than grep, so prose cannot trip it; verified to fail on a planted violation |
| 6 Credential chain | D4a, P16, P17, P18 | satisfied, docs drifted | An explicit `--token-file` now beats the environment (commit 9f0ce76, regression-tested). D4a, the README table, `USAGE`, and `.env.example` still say environment first |
| 7 `whoami` | PRD 9.1, D19 | satisfied | |
| 8 Cache | D7 | satisfied | Also caches `email`, which D7 does not list and nothing reads (low) |
| 9 Directory, `users`, `channels` | D5, D6, D7, PRD 9.2 | partially satisfied | `--include-archived` is documented in the command help and cannot show anything (medium) |
| 10 mrkdwn | D10, P8 | satisfied | Every D10 row has a test; unescape-after-parse tested |
| 11 Whole-message rendering | D9, P8, PRD 7 | satisfied | Every fixture shape renders non-empty; `_equivalent` can drop text in one direction (low) |
| 12 Time windows | D14, P3 | satisfied | Bare numbers refused, inverted ranges exit 2 |
| 13 Resolver and `resolve` | D6, D8, P10, PRD 9.3 | satisfied | Verified with `conversations.members` per the D6 revision; the tie report prints IDs and labels but not the member lists D6 step 5 promises (low) |
| 14 Formatters | D12, D13, D19, P6, P7, P9 | satisfied | jsonl, json, and raw live in `formatters/__init__.py`; the plan checkbox and the architecture layout name four files that do not exist (low) |
| 15 `conversation` | D11, D15, D16, P1..P5, PRD 9.4, 9.5 | partially satisfied | Continuation after ceiling truncation is wrong or absent (high); replies not paginated (medium); `thread_broadcast` emitted twice (medium) |
| 16 `thread` | D13, P4 | partially satisfied | Truncation keeps the root, as designed, but the reported cursor does not resume from the cut, and there is no `--cursor` flag to consume it (part of the high finding) |
| 17 Audit log | D20 | satisfied | Whole serialized line scrubbed; content never recorded; file permissions not restricted (low) |
| 18 Live smoke | D22 | satisfied | Skips cleanly without the opt-in variable |
| 19 README and CLAUDE.md | PRD 9 | partially satisfied | README status and "not built" table are stale after Step 20 (medium) |
| 20 Live verification | PRD 9, definition of done | satisfied | Recorded in the roadmap with measured results; the README was not updated in the same commit |

## Drift findings

- **high** | `src/slackline/commands/conversation.py:182-193, 233-249`; `src/slackline/commands/thread.py:153-187` | After `--max-messages` truncation, `next_cursor` is the cursor of the page *after* the one that was cut, so `--cursor` continuation skips every message that was left on the truncated page. Reproduced offline: a 4-per-page history truncated at 2 reported cursor `c2`, and continuing with it delivered page 2 while m2 and m3 from page 1 were never delivered. When truncation lands on the last page, the record says `has_more: true` with `next_cursor: ""`, so there is nothing to continue with. `thread` has the same shape and no `--cursor` flag at all, although its help text says `--all-pages` follows cursors. D16 and PRD P5 are the promises; PRD success criterion 5 ("continue if not") is only met at page boundaries | Stop at page boundaries, or record the `ts` of the last delivered message and resume from it via `latest`/`oldest` (D14 already produces those bounds); never emit `has_more: true` without a usable continuation; add `--cursor` to `thread`; write the continuation test first and watch it fail
- **medium** | `src/slackline/commands/conversation.py:210-216` | `--threads full` fetches replies with one `conversations.replies` call at `--limit` and ignores its `response_metadata.next_cursor`. A thread with more replies than the limit is cut silently: no `filtered` count, no cursor, no warning. Invariants 4 and 5 in CLAUDE.md say nothing is dropped silently and every subset is counted | Paginate replies through `client.paginate`, bounded, and report anything not fetched in the `page` record
- **medium** | `src/slackline/commands/conversation.py:200-225` | A `thread_broadcast` reply appears in channel history and in `conversations.replies`, and the command emits it from both, so it is rendered twice under `--threads full`. Reproduced offline with the fixtures | Deduplicate by `ts` when inlining replies
- **medium** | `src/slackline/directory.py:148-154`; `src/slackline/commands/channels.py:23, 61-62, 71-72` | The directory is fetched with `exclude_archived="true"`, so `archived` is always false and `channels --include-archived` is a documented flag that changes nothing. An agent asking for archived conversations is told there are none | Either fetch with `exclude_archived=false` and filter locally, or remove the flag from the help text
- **medium** | `README.md:15-17, 304, 308` | The README says the tool "has not yet been walked end to end against a live workspace" and lists the live pass as "not yet done"; commit 0ab2451 did that walk and updated the roadmap but not the README. The same table says multiple conversations per invocation are not built, but `--channel` and `--conversation` are repeatable and `conversation` loops over every match, verified offline. "Docs move with the code" is a stated principle of the phase | Update the status paragraph and the table; decide whether multi-conversation reads are a supported feature now or should be refused until Phase 2.0 designs the per-conversation framing
- **medium** | `docs/architecture/architecture.md` D4a; `README.md:63-71`; `src/slackline/cli_read.py:50-55`; `.env.example` | All four document the credential order as environment first, then `--token-file`. The code makes an explicit `--token-file` win over the environment (`auth.py:172-179`), deliberately and with a regression test. The behavior is the better one; the documentation of a credential contract is wrong in four places | Document the actual rule: an explicit `--token-file` is an instruction and overrides the ambient environment; otherwise environment first
- **low** | `docs/architecture/architecture.md` section 1; `docs/plans/slackline-read-mvp.md` Step 14; `src/slackline/directory.py:176-179` | The layout lists `formatters/jsonl.py` and `formatters/raw.py`, which do not exist, and omits `transcript.py` and `commands/_common.py`. The plan's Step 14 checkbox claims four formatter files. The D3 code sample omits `conversations.members` (the prose after it covers the addition). A comment in `directory.py` still says membership is verified against `conversations.info` | Correct the layout and the comment; note in Step 14 where the formats actually live
- **low** | `src/slackline/directory.py:180-183`; `src/slackline/commands/resolve.py:71-74`; `src/slackline/resolve.py:185-194` | A group DM's `members` are derived from the handles in its name, so a conversation found by the fallback scan (stale name, the case D6 exists for) reports an incomplete member list in `resolve` output. The ambiguity report prints IDs, labels, and names, not the member lists D6 step 5 promises | Use the verified `conversations.members` result, which the resolver already has and caches, when it is available
- **low** | `src/slackline/render.py:303-321, 356-361` | `_equivalent` treats the two renderings as duplicates when either alphanumeric essence contains the other. When `text` contains more than `blocks`, the block rendering is kept and the extra text is lost, which is the direction the docstring says must not happen | Prefer blocks only when the text essence is contained in the block essence; keep both otherwise
- **low** | `src/slackline/commands/conversation.py:188` | `--max-messages` counts top-level messages only; with `--threads full` the output can be many times the ceiling. D15 calls it a "ceiling across all pages" without saying whether replies count | Document it, or count replies toward the ceiling
- **low** | `src/slackline/api.py:286-293` | `Retry-After` is honored with no upper bound. A broken or hostile intermediary can park an agent for as long as it likes; the wait is announced but not limited | Cap at a few minutes and fail with a readable error beyond that
- **low** | `src/slackline/directory.py:74` | The user cache stores `email`, which nothing in the package reads and which D7's "names, IDs, membership" does not cover. The directory is 0o700, so exposure is limited to the same account | Drop the field, or add it to D7 and the README's description of what is cached
- **low** | `src/slackline/audit.py:84-88` | The audit log is created with default permissions while the cache directory is deliberately 0o700. The log carries argv (`--match` strings, channel names) and the conversation IDs read | Create the state directory 0o700 and the file 0o600
- **low** | `tests/test_cmd_whoami.py:10-17`; `tests/fixtures/directory.json`; `tests/test_resolve.py:5, 111-112`; `tests/test_directory.py:129`; `docs/architecture/architecture.md` D6 | The roadmap gate asks that no real colleague name entered the repository via fixtures. Message content and surnames are synthetic, but the fixture handles `joan` and `kim` are the first names the PRD uses as its running example, the owner's real handle, name, user ID, team ID, team name, and workspace URL appear in `test_cmd_whoami.py`, and the real handles `lee`, `bea`, and `deactivateduser000001` appear in D6 and in test docstrings quoting it. Acceptable for a proprietary internal repository | Treat as a Phase 3.1 blocker: scrub before any publication decision, and decide then whether D6 keeps the real names as evidence or replaces them
- **low** | `src/slackline/commands/thread.py:37-39`; `README.md:183` | Both say `--ts` may be the `ts` of any message in the thread. Slack's `conversations.replies` given a reply's `ts` returns that one message, and the `root_ts` inference cannot recover the root from it. Not verified live during this gate | Verify against the live workspace; if confirmed, either say "the root's ts" or resolve a reply's `thread_ts` first
- **nit** | `src/slackline/cli_read.py:370-372` | `KeyboardInterrupt` exits 1, which D18 reads as an API or unexpected failure; 130 is the convention | Exit 130, and add it to D18 if it matters
- **nit** | `src/slackline/cli_read.py:146-178` | All records are held in memory and rendered once at the end, so PRD P7's "streamable" JSONL is streamable to consume but not produced as a stream. Bounded by `--max-messages`, so harmless today | Note it in D19, or flush per record once the continuation fix lands

## Security

Triage: the phase touched secret handling (`auth.py`, `errors.py`, `audit.py`),
subprocess construction (`auth.py` Keychain and `op`), file handling and permissions
(`cache.py`, `audit.py`), and parsing of untrusted network content (`api.py`, `render.py`).
Full review performed on those areas.

No critical or high security findings.

- Token hygiene holds. The token travels only in the `Authorization` header
  (`api.py:225-229`, tested), there is no `--token` flag (tested by source inspection),
  every `SlacklineError` scrubs on `__str__`, unexpected exceptions are scrubbed before
  rendering (`cli_read.py:373-377`), and the audit line is scrubbed after serialization
  (`audit.py:83`). A bot or app token is refused before use (`auth.py:120-128`).
- Subprocesses are argv lists with fixed commands and timeouts, no shell
  (`auth.py:58-63, 73-78`). `SLACKLINE_OP_REF` is passed as a positional argument; a value
  beginning with `-` would be read by `op` as a flag, which is harmless.
- `.env` in the current working directory is the last credential source
  (`auth.py:102-103`). An agent started in a directory it does not control can be handed a
  different token and read a different workspace without noticing. This is documented as a
  development convenience, the environment variable wins when set, and `--token-source
  env` disables the chain for unattended jobs. Low; the README's agent section already
  says to pin the source.
- Rendered message text reaches stdout with control characters intact
  (`render.py`, `formatters/text.py`). A message containing terminal escape sequences would
  be interpreted by a human's terminal. Slack content is untrusted by definition. Low;
  strip C0 controls other than newline and tab in the text formatter.
- Cache filenames embed `team_id` from the `auth.test` response (`cache.py:59-62`). Slack
  team IDs are `T` followed by alphanumerics, and the value comes from the authenticated
  API rather than the caller, but nothing checks the shape before it becomes a path
  component. Nit; validate against `^[A-Z0-9]+$`.
- `Retry-After` unbounded (listed above as low).
- Audit log permissions (listed above as low).
- TLS verification is urllib's default and is not overridden. No content is cached. The
  cache API refuses anything but directory kinds (`cache.py:94-98`, tested).

## Fix list (seeds the next planning session)

1. Make continuation after a ceiling truncation correct in `conversation` and `thread`:
   never report `has_more` without a usable continuation, never let `--cursor` skip
   messages left on the truncated page, and give `thread` a `--cursor`. Write the
   continuation test first; it fails today.
2. Paginate `conversations.replies` under `--threads full`, bounded, and count anything
   not fetched in the `page` record.
3. Deduplicate `thread_broadcast` replies by `ts` when inlining threads.
4. Make `channels --include-archived` work or remove it from the help text.
5. Correct the credential precedence in D4a, the README table, `USAGE`, and
   `.env.example` to match `auth.py`.
6. Bring the README up to date: the live pass happened on 2026-08-28; decide whether
   repeatable `--channel` and `--conversation` are a supported feature now.
7. Report verified member lists in `resolve` output and in the ambiguity report.
8. Restrict audit log permissions to the owner, and drop or document the cached `email`
   field.
9. Cap `Retry-After`.
10. Fix the direction of `_equivalent` so text is never dropped in favor of a subset of
    itself.
11. Correct the architecture layout, the D3 sample, the `directory.py` comment, and the
    Step 14 note.
12. Strip terminal control characters in the text formatter; validate `team_id` before it
    becomes a filename.
13. Verify the "any message's ts" claim for `thread` against the live workspace.
14. Record the identifier and first-name exposure in tests and D6 as a Phase 3.1 blocker.
