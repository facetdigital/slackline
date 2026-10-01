# Phase Gate: Read-Only MVP (re-run)

Date: 2026-08-30
Auditor: Phase gate review
Diff range: a7e4bd1..e1539b01c784bdb4de136f50b96cbc827082035e
Verdict: PASS_WITH_FINDINGS

## Summary

The first gate returned PASS_WITH_FINDINGS on 2026-08-28 with one high finding: after a
`--max-messages` truncation the reported cursor skipped the rest of the cut page, and a
cut on the last page reported `has_more: true` with no cursor. Four merged PRs (#10 to
#13) executed a 20-step plan against that gate's 14-item fix list. This re-run verified,
by reproduction rather than by checkbox, that the high finding is closed: each request now
asks Slack for the remaining budget, so the ceiling lands on a page boundary and Slack's
own cursor is exact. Continuation through `main()` with a fake transport skipped nothing
and repeated nothing, including after a cut on the final page, and `thread` now accepts
`--cursor`. All 14 items of the first gate's fix list are closed, and every one of the 20
plan steps is satisfied or satisfied with a noted qualification. The read-only guard,
token scrubbing, and D18 exit codes were re-proven the hard way.

The verdict is not PASS because two medium findings remain. First, the offline suite is
green in this working tree only because the gitignored `.env` supplies a credential:
`test_the_cli_records_a_failed_invocation` fails in a clean checkout (exit 3 rather than
2), which breaks invariant 10 and reveals that `cli_read._dispatch` resolves a token
before the command parses its arguments. The phase documented this rather than fixing it.
Second, `transcript.collect`, the function whose docstring says its whole job is to be
bounded, loops without limit under `--all-pages` when a server keeps handing back a cursor
with empty or fully filtered pages; reproduced offline at 5,000 requests and counting.
Both are small fixes and neither undermines a core promise.

## Test truthfulness

`.venv/bin/python -m pytest -q`: 434 passed in 0.59s. `tests/live`: 3 skipped, each with
the stated reason (`SLACKLINE_LIVE_TEST` unset). No `xfail`, no `pending`, no marker
skips outside `tests/live`. No TODO, FIXME, HACK, or XXX markers in `src`, `tests`, or the
docs. No emdash characters were added anywhere in the diff range (the only ones in the
tree are in the pre-existing `initial-idea.md`, outside the range and outside the rule).

Deleted lines in pre-existing test files, from `git diff a7e4bd1..HEAD -- tests/`: the
`FakeClient.call` and `paginate` bodies that Step 1 rewrote (behavior widened, every prior
caller still passes), and the `try/except ArgumentError: continue` that Step 19 removed
from the help-text test. Nothing else was deleted or weakened. Net change: 20 test files,
+1635/-7.

The roadmap gate item's five specifics, each verified rather than assumed:

- **The allowlist guard fails when a write method is introduced.** A temporary
  `src/slackline/_gate_probe.py` containing `client.call("chat.postMessage", ...)` made
  `test_every_slack_method_in_the_package_is_read_only` fail with
  `src/slackline/_gate_probe.py:2 uses 'chat.postMessage', which is not read-only`. The
  probe was deleted; `git status --porcelain` was empty afterward.
- **The token-scrubbing tests fail when scrubbing is disabled.** A pytest plugin object
  passed to `pytest.main(plugins=[...])` replaced `errors.scrub` with the identity function
  and neutered `errors._TOKEN_RE` in-process, no files edited. Result: 10 failed, 424
  passed. The failures are exactly the scrubbing tests: both audit-log scrub tests, all
  four parametrized cases of `test_tokens_are_scrubbed_from_error_messages`, the URL/JSON
  case, and all three URL-safe-character cases.
- **No test passes by asserting broken behavior.** Every test file changed in the range was
  read. One test deserves a note: `test_a_broadcast_can_appear_on_both_sides_of_a_page_boundary`
  pins a known duplicate (a `thread_broadcast` whose root sits on the next page is emitted
  from history on page one and again under its root on page two). Its docstring says it is
  a documented qualification rather than a defect, the README and the `conversation`
  module docstring say the same, and the alternative it guards against (a silent drop) is
  the worse failure under D9. This is a conscious pin, not a test asserting a bug.
- **No real message content or colleague name entered the repository via fixtures.** The
  three rows added to `tests/fixtures/directory.json` (`C102 old-project`, `C103
  engineering` archived, `G399 mpdm-sam--joan--kim-2`) and the one message added to
  `messages.json` (`text_superset_of_blocks`) are synthetic. The only real-looking
  identifier on an added test line is `T0ABC1234` in `test_cache.py`, which was already in
  that file before the range (2 occurrences at a7e4bd1). The first gate's low finding on
  first-name handles and workspace identifiers is unchanged and is now recorded in the
  roadmap's Phase 3.1 section as required.
- **Exit codes match D18 in practice, and a truncated read exits 0.** Through
  `cli_read.main()` with `api._urllib_transport` replaced by a canned transport and a stub
  `SLACK_USER_TOKEN`: 2 (bad flag, no selector), 3 (`invalid_auth`), 4 (ambiguous channel,
  ambiguous user), 5 (unknown name, `channel_not_found` from the API), 7 (a command coerced
  into calling `chat.postMessage`), 130 (`KeyboardInterrupt`), 1 (unexpected exception,
  with the token-shaped string in its message rendered as `xox***`). stdout was empty on
  every failure path. A `--max-messages 5` read over 12 messages exited 0 with
  `has_more: true`, `truncated_by: "max-messages"`, and a non-empty cursor.

The item the first gate rated high, reproduced with the same fake transport:
`--max-messages 5` delivered 4 messages (the fake's first page holds 4; the collector asked
for 5, got 4, then asked for 1 more from page two and stopped on a page boundary) and a
`--cursor --all-pages` continuation delivered the other 8: union equal to all 12 `ts`
values, intersection empty, `has_more: false` and an empty cursor at the end. A cut on the
last page (`--all-pages --max-messages 10` over 12) reported `has_more: true` with a
usable cursor, and continuing from it delivered exactly the 2 remaining. `thread` was
checked the same way through `FakeClient` with a mid-page cut at 3 of 8: union complete,
overlap empty. `test_continuation_e2e.py` and `test_transcript_collect.py` cover the same
ground in the suite.

Ways the green suite misleads, found by doing rather than reading:

- **The suite needs a secret.** `SLACK_USER_TOKEN` is unset in this gate's environment.
  Running the suite from a directory with no `.env` (`--rootdir` and `-c` pointed at the
  repo) gives 1 failed, 433 passed:
  `test_the_cli_records_a_failed_invocation` asserts exit 2 and gets 3. In the repo root
  the same test passes because `auth.resolve_token` falls through to `./.env` (and would
  also pass on a machine with a `slackline` Keychain entry, since the default suite runs
  `security find-generic-password` on the way there). CLAUDE.md invariant 10 says the
  offline suite needs no secret; the phase amended invariant 10 to document the exception
  instead of removing it. Medium, below.
- **No test bounds `collect` against a cursor chain.** Reproduced offline: a client that
  returns `messages: []` with a fresh cursor every time, and one that returns a full page
  of `channel_join` every time, both drive `collect(..., max_messages=500,
  all_pages=True)` past 5,000 requests. The roadmap records this as a deferred follow-up.
  Medium, below.
- **The fake does not model `conversations.replies` returning the parent in addition to
  `limit` replies**, which the roadmap records as observed live on 2026-08-30. So
  `test_a_long_thread_truncates_at_the_ceiling_and_still_exits_0` asserts `len == 2`
  for `--max-messages 2`, a count the live API produces as 3. The collector's over-delivery
  guard keeps the cursor exact either way. Low, below.
- C1/DEL escaping in `format_json` and `format_raw` is tested only through `format_jsonl`;
  all three share `_dumps`, so the risk is nil today. Nit.
- Coverage is not measured in this repository. Test count rose from 315 to 434.

## Citation walk

| Plan step | Satisfies | Status | Notes |
|---|---|---|---|
| 1 FakeClient honors limit and cursor | D21; prerequisite for Gate-1 | satisfied | `tests/conftest.py:_page/_locate`; mid-page cursor `fake:<page>:<offset>`; `test_fake_client.py` pins the contract and the loud-failure cases; `paginate` also honors `exclude_archived` so Step 8 could observe fetch versus filter |
| 2 Shared bounded collector | D15, D16, P5, PRD 9.5; Gate-1 | satisfied, with a qualification | `transcript.py:collect` sizes each request to the remaining budget; never cuts a fetched page; over-delivery guarded (`remaining <= 0`); `max(1, ...)` keeps the wire limit positive. Not bounded against an unending cursor chain (medium finding) |
| 3 `conversation` uses the collector | D15, D16, P5, PRD 9.5; Gate-1 | satisfied | `conversation.py:194-211`; `has_more = bool(next_cursor)` (line 282); `--cursor` with several conversations refused (lines 170-178); e2e through `main()` in `test_continuation_e2e.py` |
| 4 `thread` uses the collector and `--cursor` | D13, D16, P4; Gate-1 | satisfied | `thread.py:168-179, 256-260`; continuation pages drop a repeated parent and mark every message as a reply; empty continuation page is `NotFound`, not an empty thread |
| 5 Paginate replies, report unfinished threads | D9, D11, P4, invariants 4 and 5; Gate-2 | satisfied | `conversation.py:247-261`; `incomplete_threads` in the page record and on stderr; text formatter line `n thread(s) not fully expanded, pass --all-pages`; help text documents the per-thread bound. D15 amended to match |
| 6 `thread_broadcast` rendered once | D9, P8; Gate-3 | satisfied | `emitted` set per conversation, `conversation.py:218-233, 262-267`; ts-less messages never enter the set; cross-page duplicate consciously pinned and documented |
| 7 Multi-conversation reads supported | P1, P2, PRD 9.4; Gate-6 (capability half) | satisfied | Tests pin header and page per conversation and the audit list; README, help, and the roadmap Phase 2.0 bullet updated |
| 8 `--include-archived` works | D5, D7, P1, PRD 9.2; Gate-4 | satisfied | `directory.py:151-157` fetches with `exclude_archived=False`; `find_conversations_by_name` prefers live rows (`live or exact`); the resolver announces archived namesakes; `resolve` output carries `archived`. D8 amended with the carve-out, CLAUDE.md invariant 6 amended to match |
| 9 `resolve` reports verified members | D6 step 5, D8, PRD 9.3; Gate-7 | satisfied | `resolve.py:249` replaces name-derived members with the memoized verified set; tie report prints member handles (`resolve.py:237-242`); `commands/resolve.py` sorts handles; `directory.py:_shape` comment corrected |
| 10 Audit log owner-only | D20; Gate-8 | satisfied | `audit.py:94-127`: mkdir decides "created", file mode fixed on the descriptor via `fchmod` before the write, symlinks and non-regular files left alone, no `fchmod` platform still gets its line. Nine tests including the check-then-create race. D20 amended |
| 11 Cap `Retry-After` | D17; Gate-9 | satisfied | `api.py:36, 296-302`; 900s refused with a readable error and no sleep, 120s honored exactly, negative and NaN clamped to 0. D17 amended |
| 12 Strip terminal controls from text output | P8, P11; Gate-12 | satisfied; citation loose | `formatters/text.py:18-22`, applied per message text and at the `render_records` seam; `Context.warn`, `exc.report()`, and the unexpected-failure path cleaned in `cli_read.py`; JSON formatters escape DEL and C1 (`formatters/__init__.py:23-28`). P11 is about which stream carries what, not about control characters; the requirement actually served is P8 plus the first gate's security review |
| 13 Validate `team_id` before it names a file | D7; Gate-12 | satisfied | `cache.py:58-59`, `re.fullmatch(r"[A-Za-z0-9_-]+")`; `..`, `/`, spaces, and empty refused; parametrized tests |
| 14 Never drop `text` for a subset of itself | D9, P8; Gate-10 | satisfied | `render.py:_side_to_keep` returns which side contains the other, or neither; pre-existing equivalence tests unmodified and still green |
| 15 `thread --ts` given a reply's ts | D13, P4; Gate-13 | satisfied | `thread.py:196-243`; one extra call, announced; a filtered reply still redirects via a one-message probe; root known after redirect even when every reply is filtered; opt-in live check added to `tests/live/test_smoke.py` with its two variables documented |
| 16 `KeyboardInterrupt` exits 130 | D18, P12; gate nit | satisfied | `cli_read.py:375-378`; `EXIT_CODES`, D18 table, and README table all list 130; verified through `main()` |
| 17 Credential precedence documented | D4a, P16, P18; Gate-5 | satisfied | D4a rewritten (`--token-file` outside the chain, consulted first, missing file is an error); README table, `USAGE`, `.env.example`, CLAUDE.md layout row, and the no-credential error message all agree; `test_the_no_credential_message_states_the_real_order` pins the message |
| 18 README status, layout, stale comments | D3, D7, D15, D19; Gate-6 (README half), Gate-8, Gate-11, Gate-14 | satisfied | README status paragraph and not-built table current; directory-cache section added; architecture layout lists `_common.py`, `transcript.py`, `formatters/__init__.py`; D3 sample has `conversations.members`; D7 lists `email` and `archived`; D19 states render-once; `directory.py` comment names `conversations.members`; MVP plan Step 14 annotated; Phase 3.1 identifier list recorded |
| 19 Remove the vacuous branch | roadmap "no test passes by asserting broken behavior" | satisfied | `test_every_command_exposes_the_help_text_dispatch_relies_on` now fails on a missing command |
| 20 Close out | PRD section 9; roadmap definition of done | satisfied, with a qualification | All checkboxes checked; roadmap moved the four PRs to Completed with dates; suite green in this tree. The live close-out results are recorded in PR #13's description and partly in the roadmap's Phase 2.0 bullet (the two live observations); this gate could not verify them offline. "Full suite green" holds only with the `.env` present (medium finding) |

Original MVP plan steps revisited by this phase: Step 14 (formatters) now carries the
as-built note; Step 15 (`conversation`) and Step 16 (`thread`), which the first gate
rated partially satisfied, are now satisfied per rows 3 to 6 and 15 above; Step 9
(`channels`), partially satisfied before, is satisfied per row 8; Step 19 (README) is
satisfied per row 18.

## First gate's fix list

| Item | Status | Evidence |
|---|---|---|
| 1 Continuation exact after truncation; `has_more` never without a cursor; `thread --cursor` | closed | Reproduced through `main()` and through `FakeClient`: no gap, no overlap, cursor present after a last-page cut; `thread.py:_parse` accepts `--cursor`; `has_more = bool(next_cursor)` in both commands |
| 2 Paginate `conversations.replies` under `--threads full`, count what is not fetched | closed | `conversation.py:247-261`; `incomplete_threads` in the page record, on stderr, and in the text trailer; tests for one page, all pages, and zero incomplete |
| 3 Deduplicate `thread_broadcast` | closed | `emitted` set; `test_a_broadcast_reply_is_rendered_once_under_its_root` and two siblings |
| 4 `--include-archived` works or is removed | closed | Directory fetches archived rows; `test_include_archived_shows_them`; live rows win a name and the archived namesakes are announced |
| 5 Credential precedence corrected in D4a, README, `USAGE`, `.env.example` | closed | All four plus the error message and CLAUDE.md agree with `auth.py:171-179` |
| 6 README current; multi-conversation reads decided | closed | Status paragraph cites the 2026-08-28 live pass; not-built rows removed; multi-conversation reads documented, tested, and noted in the roadmap |
| 7 Verified member lists in `resolve` and in the tie report | closed | `resolve.py:237-249`; `test_cmd_resolve.py` sees handles from the verified set; tie lines carry `members:` |
| 8 Audit log owner-only; cached `email` dropped or documented | closed | `audit.py` 0600/0700 with tests; D7 and README name `email` and `archived` as cached fields |
| 9 Cap `Retry-After` | closed | `MAX_RETRY_AFTER_SECONDS = 300`; refused beyond, honored within; D17 amended |
| 10 `_equivalent` direction | closed | `_side_to_keep`; `text_superset_of_blocks` fixture kept in full, overlap not repeated |
| 11 Architecture layout, D3 sample, `directory.py` comment, Step 14 note | closed | All four corrected; `grep` for `jsonl.py`, `raw.py`, and `conversations.info` in the named files returns nothing |
| 12 Strip control characters; validate `team_id` | closed | `_clean` in text and stderr paths, `_dumps` in JSON paths; `Cache.__init__` refuses non-ID strings |
| 13 Verify "any message's ts" for `thread` | closed | Code now redirects from a reply's ts to its root with one extra call; opt-in live test added. The live confirmation itself is in PR #13's description, not verifiable offline |
| 14 Record identifier exposure as a Phase 3.1 blocker | closed | Roadmap Phase 3.1 "Before any publication" list names the three locations |

## Drift findings

- **medium** | `tests/test_audit.py:204-214`; `src/slackline/cli_read.py:342-351`; `CLAUDE.md` invariant 10; roadmap Phase 2.0 CI bullet | The offline suite is green only when a credential is on disk. In a directory with no `.env` and no `SLACK_USER_TOKEN`, `test_the_cli_records_a_failed_invocation` gets exit 3 where it asserts 2 (verified: 1 failed, 433 passed). In the repo root it passes because `resolve_token` reads the gitignored `.env`, and on the way it runs `security find-generic-password`, so the "offline, no secret" suite spawns a Keychain lookup and reads a real token into memory on a developer machine. The phase documented the exception in CLAUDE.md and the roadmap rather than fixing it, so invariant 10 now carries a standing carve-out. Underneath is a real contract defect: `_dispatch` resolves the token before `command.run` parses its arguments, so an agent with a bad flag and no credential is told "no token" (3) instead of "bad argument" (2), against D18's meaning of 2 | Parse command arguments before resolving a credential (a `parse(argv)` step each command exposes, or a two-phase `run`), then make the test assert exit 2 with `SLACK_USER_TOKEN` deleted and `dotenv_path` pointed at nothing; write the test first and watch it fail in a clean directory. Remove the carve-out sentence from invariant 10 once green
- **medium** | `src/slackline/transcript.py:193-219`; `src/slackline/commands/conversation.py:247-258` (reply expansion with `max_messages=None`) | `collect` under `all_pages=True` has no page ceiling. Reproduced offline: a server that answers every request with `messages: []` and a fresh cursor, or with a full page of filtered `channel_join` messages and a fresh cursor, drives the loop past 5,000 requests with the budget unspent. The reply-expansion call is unbounded by construction (`max_messages=None`). Real Slack does not produce an infinite cursor chain, so the exposure is the same hostile or broken intermediary that the phase capped `Retry-After` for (D17); leaving the loop open is inconsistent with that decision, and the docstring at `transcript.py:1179-1185` calls this "the one place whose whole job is to be bounded". Two review passes flagged it and deferred it to the roadmap with no test | Add a ceiling on pages per `collect` call (or on consecutive pages that kept nothing), stop with a distinct `truncated_by` value such as `"page-limit"` and the cursor, and surface it on stderr. Write the failing test first with the empty-page client used in this gate
- **low** | `src/slackline/audit.py:108-116` | The log is opened with `O_CREAT` and no `O_NOFOLLOW`, and `test_a_symlinked_log_is_appended_to_but_never_re_permissioned` pins following the link as intended. If `SLACKLINE_AUDIT_LOG` points into a directory another account can write (`/tmp`, a shared project directory), a pre-planted symlink named `audit.jsonl` makes Slackline append a JSON line to any file the user can write. The default location is `0700` under the user, which is why this is low | Either open with `O_NOFOLLOW` and treat `ELOOP` as "not a file Slackline created" (a warning, per D20), or refuse a parent directory that is group- or world-writable, or at minimum say in the README that the path must be in a directory only the user can write
- **low** | `src/slackline/cache.py:69-76` | `_ensure_root` runs `chmod 0700` on `SLACKLINE_CACHE_DIR` whether or not Slackline created it. Pointing the variable at `$HOME` or a shared checkout silently changes that directory's permissions. This phase established the opposite rule for the audit log ("a read-only tool does not change permissions it did not set", `audit.py:89-93`); the cache predates it and was not brought into line even though `cache.py` was edited in the range | Apply the same mkdir-decides pattern: tighten only a directory this process created; leave a pre-existing one alone and warn if it is looser than `0700`
- **low** | `src/slackline/commands/conversation.py:273-274` | `--order newest` reverses the flat record list, so under `--threads full` every reply is emitted above its root, indented under nothing. Verified offline: reply `...13` precedes parent `...12`. The comment at line 212 says the ascending sort exists "so replies land under the right root"; the reversal undoes that. Pre-existing since Phase 1.0 and missed by the first gate | Reverse the order of root groups, keeping each root followed by its replies; test with a thread plus one top-level message under `--order newest`
- **low** | `tests/conftest.py:FakeClient._page`; `tests/test_cmd_thread.py:213-219`; roadmap Phase 2.0 follow-ups | The roadmap records that live `conversations.replies` returns the parent in addition to `limit` replies, so `thread --max-messages N` returns N+1 messages on the first page. The fake serves exactly `limit` items, so the offline ceiling test asserts a count the live API does not produce, and `HELP` says "Ceiling on messages returned" without the qualification | Model the parent-plus-limit shape in the fake for `conversations.replies` when no cursor is given (write the test first), then either ask for `N-1` on the first page or document the overshoot in `HELP` and D15
- **nit** | `docs/architecture/architecture.md` D4b (line 163) | States the scrub pattern as `xox[abposr]-[A-Za-z0-9-]+`; `errors.py:29` uses `(?:xox[a-z]|xapp)-[A-Za-z0-9%/+=._-]+`. The narrower pattern is the one the tests prove insufficient | Quote the real pattern, or describe it in prose ("any `xox?-` or `xapp-` prefix and the URL-safe run after it")
- **nit** | `src/slackline/commands/conversation.py:212-213` | "Slack returns history newest-first" is contradicted by the live observation the roadmap records for `oldest` without `latest`; the sort makes it moot but the comment now asserts something known to be false | Say "in either order" and keep the sort
- **nit** | `src/slackline/commands/conversation.py:295-305`; `src/slackline/commands/thread.py:291-302`; D16 sample | The two `page` records differ in shape (`thread` has `thread_ts` and no `incomplete_threads`); D16's sample shows only the `conversation` shape; `transcript.py` exists so the two commands do not drift into two contracts, and the roadmap already lists a `page_record()` helper | Build the record in one place with `incomplete_threads` present in both (0 for `thread`) and note the `thread_ts` extra in D16
- **nit** | `src/slackline/resolve.py:150-156` | The set-aside warning says "archived group DM with a matching name" on both paths, including the fallback membership scan where the name did not match by definition | Word it by member set: "archived group DM with these members"
- **nit** | `docs/plans/development-roadmap.md` "Gate fixes PR 4" entry; plan Step 20 | The Step 20 live results (continuation with no gap, `--threads full`, `--include-archived`) are recorded in PR #13's description only. The Phase 1.0 entry restates its live results in the roadmap; this one says "the close-out live check" and stops. A later session cannot see what was measured without leaving the repository | Add one paragraph of measured results to the PR 4 entry, matching the Phase 1.0 entry
- **nit** | `tests/test_formatters_text.py:48-56` | C1/DEL escaping is asserted only through `format_jsonl`; `format_json` and `format_raw` share `_dumps` but nothing pins that they keep doing so | One assertion each through `format_json` and `format_raw`

Scope leakage: none found. Every change in the range traces to a plan step or a review
finding recorded in a commit message. The D8 carve-out, the D4a revision, and the D15,
D17, D18, D20 amendments are each reflected in code, tests, and the doc in the same PR.
Zero runtime dependencies holds (`dependencies = []`). No `--token` flag was added. The
allowlist is unchanged.

## Security

Triage: hit. The four PRs touched secret-handling documentation (`auth.py` message,
D4a, README, `USAGE`, `.env.example`), file permissions and file creation (`audit.py`),
path construction (`cache.py`), untrusted Slack content reaching a terminal
(`formatters/text.py`, `formatters/__init__.py`, `cli_read.py:warn` and the error
paths), retry handling of a server-controlled header (`api.py`), and parsing of server
data into control flow (`transcript.py:collect`, `thread.py:_root_of` and the redirect,
`resolve.py:_probe/_confirm`, `directory.py`). Full adversarial review performed on all of
those.

No critical or high security findings.

- **Read-only allowlist: not weakened.** `READ_ONLY_METHODS` is byte-identical to a7e4bd1.
  The planted-violation check above proves the guard still fires. `FakeClient` still
  enforces the same set (`test_the_allowlist_is_still_enforced`). Every new server-derived
  value that becomes a request parameter (`thread_ts` used as `ts` in the redirect,
  `next_cursor`, the `limit` computed from the budget) goes through `urlencode` in the one
  chokepoint and can only reach the four `conversations.*` read methods already permitted.
- **Token hygiene: holds.** The token still travels only in the `Authorization` header
  (`api.py:229-233`). Scrubbing was shown load-bearing (10 failures when disabled). The new
  stderr paths (`cli_read.py:167, 360, 383`) apply `_clean` after `scrub`, so control
  stripping cannot un-redact anything. The `_gate_probe` planted during this audit
  contained no token. The no-credential error message names no value.
- **Audit log (`audit.py:94-127`).** Whether Slackline created the directory is decided by
  `os.mkdir` itself, so a symlink planted between an `exists()` check and the create cannot
  redirect a `chmod` (tested: `test_a_directory_that_appeared_between_check_and_mkdir_is_not_chmodded`).
  The file mode is set on the open descriptor with `fchmod`, only for a regular file, and
  only when the path is not a symlink, so a FIFO, device, or link target is never
  re-permissioned. The remaining exposure is the symlink-follow on open in a shared
  directory (low, above). The `is_symlink()` check runs after `open`, so a link swapped in
  between the two would skip the `fchmod` (fail-safe) rather than chmod a stranger's file;
  a link present at open time and swapped out before the check would `fchmod` the target,
  which the user must already own for the append to succeed, so the impact is tightening a
  file the user owns to `0600`. Nit-level. Grandparent directories are created at umask
  permissions; only the immediate parent is `0700`, which matches D20's wording.
- **Cache paths (`cache.py:58-67`).** `team_id` is validated with a full-match character
  class before it becomes a filename; `..`, separators, whitespace, and empty are refused
  with `ValueError`. The kind is restricted to `KINDS`. `SLACKLINE_CACHE_DIR` is
  user-controlled and joined as a `Path`, which is expected. The unconditional `chmod` of
  that root is the low finding above.
- **Terminal output (`formatters/text.py:18-22`, `formatters/__init__.py:23-28`,
  `cli_read.py`).** C0 (except tab and newline), CR, DEL, and C1 are stripped at the seam
  every text renderer passes through and in every stderr path a Slack-supplied string can
  reach (labels in ambiguity reports, `_probe` warnings, the `page` record's cursor). JSON
  output escapes DEL and C1 (`json.dumps` already escapes C0), which stays valid JSON and
  round-trips (tested). Not covered: Unicode bidi overrides and other non-control
  confusables, already recorded in the roadmap as a follow-up. The order `_clean(scrub(...))`
  is correct; the reverse would let a control character split a token so it survives the
  regex.
- **Retry handling (`api.py:290-306`).** `Retry-After` above 300s is refused with an error
  before any sleep; negative and NaN values become 0; a non-numeric value falls back to
  the base backoff. `float("inf")` is refused by the `>` comparison. The 5-attempt ceiling
  and the 32s backoff cap are unchanged.
- **Server-controlled loop bounds.** `collect` trusts `limit` on the wire and guards
  against over-delivery, but not against an unending cursor chain (medium, above). The
  resolver's fallback scan is still capped at `MAX_MEMBER_PROBES = 60`. `_confirm` skips
  archived candidates once a live one confirms, reducing probes. Thread redirect is at most
  two extra calls (`probe` with `limit=1`, then the root read), never recursive: the
  redirect only fires on the first pass and the second pass does not re-derive the root.
- **`.env` in the working directory** remains the last credential source; unchanged from
  the first gate's low note, still documented as a development convenience. This gate did
  not read the file.
- **Subprocesses** (`auth.py:58-63, 73-78`) are unchanged: fixed argv lists, no shell,
  timeouts.

## Fix list (seeds the next planning session)

1. Make the offline suite pass with no credential anywhere: reorder `cli_read._dispatch`
   so a command parses its arguments before a token is resolved (argument errors exit 2
   regardless of credentials, per D18), make `test_the_cli_records_a_failed_invocation`
   run with `SLACK_USER_TOKEN` deleted and no `.env` reachable, add a test that the default
   suite never invokes `security` or reads `./.env`, and remove the carve-out sentence from
   CLAUDE.md invariant 10. Write the test first; it fails today in a clean directory.
2. Bound `transcript.collect` under `--all-pages`: a ceiling on pages per call (or on
   consecutive pages that kept nothing), reported as a distinct `truncated_by` value with
   the cursor and a stderr line; apply it to reply expansion as well. Test first with a
   client that returns an empty page and a fresh cursor forever.
3. Decide the audit log's symlink policy: `O_NOFOLLOW` with a D20 warning on `ELOOP`, or a
   refusal of group- or world-writable parent directories, or a README constraint; then
   update `test_a_symlinked_log_is_appended_to_but_never_re_permissioned` to match the
   decision.
4. Bring `cache.py:_ensure_root` under the rule audit.py adopted: tighten only a directory
   Slackline created; warn, do not chmod, when a user-supplied `SLACKLINE_CACHE_DIR` is
   looser than `0700`.
5. Fix `--order newest` under `--threads full` so each root keeps its replies beneath it;
   test with one thread and one top-level message.
6. Model Slack's parent-plus-`limit` shape for `conversations.replies` in `FakeClient`
   (test first), then either request `N-1` on the first page of `thread` or document the
   overshoot in `HELP` and D15.
7. Documentation corrections: quote the real scrub pattern in D4b; fix the "newest-first"
   comment in `conversation.py`; record the Step 20 live results in the roadmap's PR 4
   entry; reword the `_confirm` set-aside warning for the membership-scan path.
8. Unify the `page` record through a `page_record()` helper in `transcript.py` with
   `incomplete_threads` present for both commands, and note the `thread_ts` extra in D16.
9. Add one C1-escaping assertion each through `format_json` and `format_raw`.
