# Slackline Development Roadmap

The single source of truth for what to build and in what order.

For vision, users, and the capability boundary see
[product-requirements.md](../requirements/product-requirements.md). For numbered design
decisions (D1..D22) that plan steps cite, see
[architecture.md](../architecture/architecture.md).

## Development Principles

These apply to every phase. They are not a cleanup phase at the end.

- **TDD.** Write the test expecting correct behavior first. It must fail. Then implement
  until it passes. Never modify the test afterward. A test that passes against the broken
  code proves nothing.
- **Offline tests.** The default suite runs against fixtures with no network and no
  secrets (D21). Live checks are opt-in and separate (D22).
- **Read-only is enforced, not intended.** Every phase through 2.x keeps the D3 method
  allowlist intact, and the test that guards it passing.
- **No secrets on disk beyond the gitignored `.env`.** No token in argv, in logs, in
  tracebacks, or in a fixture.
- **Zero runtime dependencies.** Adding one is an architecture decision requiring a new
  numbered entry, not a convenience.
- **One feature, one branch, one PR.** Full-history merges, never squash, never rebase.
- **Docs move with the code.** A change to behavior updates the README, the architecture
  doc if a decision changed, and this roadmap, in the same PR.

---

## Next Immediate Step

### Public release prep

**Status:** In progress. Target: public by Wednesday 2026-09-30.

**Plan:** [public-release-prep.md](public-release-prep.md), one PR, branch
`chore/public-release-prep`

Slackline goes open source. *Slackline* is the project; *slackline-read* is the
executable that exists today; *slackline-write* will someday be a second, separate
executable in the same project, never a mode or flag inside slackline-read. Slackline is
not meant to be a full MCP replacement or a full API wrapper. Being read-only by
construction is the product, and anything that weakens that promise is out of scope for
slackline-read.

Four items, in order, all before the repository goes public:
1. MIT license, `Copyright (c) 2026 Facet Digital, LLC`, matching the sibling repos, with
   `pyproject.toml` metadata to match.
2. External users in shared channels (Slack Connect) render by name, marked `(ext)`,
   instead of `unknown:` and raw `@U...` IDs: the message's `user_profile` first, then a
   cached `users.info`, then the ID.
3. A "What this is not" section in the README.
4. A publication check of the whole history, not just the working tree. Anything
   sensitive in history is reported before anything is rewritten; rewriting history
   versus a fresh public repo is the owner's call.

---

## Upcoming

### Phase 2.0: Hardening and ergonomics

**Status:** Ready to implement

**Plan:** [phase-2-0-hardening-and-ergonomics.md](phase-2-0-hardening-and-ergonomics.md),
four PRs: gate seeds, permalinks (constructed locally, D23), policy files (exit 8, D24),
completions and CI.

- `--include-permalinks` via `chat.getPermalink`, which is read-only despite the `chat.`
  prefix. Worth confirming during planning rather than assuming from the name.
- Multiple conversations in one invocation: shipped in the gate fixes (PR 1), with a
  header and page record per conversation. Any further framing polish lands here.
- `--policy` file support: allowlist and denylist of conversation IDs, plus ceilings, as a
  second layer under the executable boundary.
- Shell completions.
- CI: shipped early in v0.1.2 (`.github/workflows/tests.yml`); Step 11 of this phase's
  plan only verifies the checks appear and pass on this phase's own PRs.
- Follow-ups recorded by the gate-fix review passes, none blocking: a `page_record()`
  helper in `transcript.py` so `conversation` and `thread` stop building the record by
  hand; a page ceiling for `collect` under `--all-pages` with a budget, where a server
  returning empty pages with a cursor would loop; an optional bound on reply expansion
  under `--all-pages`; bidi override characters (U+202A..U+202E, U+2066..U+2069) are not
  stripped from text output; a FIFO planted at the audit log path blocks the open (no
  `O_NONBLOCK`), which only matters for a user-supplied path in a shared directory.

Seeds from the Read-Only MVP gate (third run), all low or nit:
- `cache.py`: bring `_ensure_root()` under the same degrade-to-warning rule as the write,
  so an unwritable, file-shadowed, or dangling-symlink `SLACKLINE_CACHE_DIR` warns and
  continues instead of exiting 1.
- `directory.py`: do not cache an index whose final page still carried a cursor after
  `api.paginate` stopped at its ceiling, or store an `incomplete` flag and warn on load.
- Roadmap follow-ups bullet above: drop the `page_record()`, `collect` ceiling, and FIFO
  items PR #14 closed; note reply expansion is bounded per thread; keep the bidi item.
- `transcript.py`: the `Collected.truncated_by` comment should include `"max-pages"`.
- Replace the `3 * constant` loop guards in the command and api tests with a fixed literal.
- README "Reading the output": one sentence on the `max-pages` stop and its stderr line.
- Extend the help-text test to require `parse` and the three-argument `run`.

### Phase 2.1: Search (maybe)

**Status:** Not planned. Build it only if users ask.

A read-only `slackline-read search` command over `search.messages`. It fits the
read-only allowlist, but it needs the `search:read` scope (added to the Slack app and
reinstalled, which only the app's owner can do), and nothing has needed it yet: channel
discovery plus bounded reads have covered every real case. Search results are
relevance-ranked and cross-channel, a genuinely different contract from `conversation`
that deserves its own design rather than being bolted onto it.

### Phase 3.0: `slackline-write`

**Status:** Not planned.

A separate executable in this project, never a flag or mode of slackline-read. It gets
its own token and scopes, and its own allowlist of write methods. Every write needs
explicit confirmation from the human running it. It shares the rendering and auth code,
but never the read-only binary's guarantees. Its design doc must explain why it is a
separate executable and not a flag (the executable boundary, PRD section 2, R3, D2).
Commands to start from: `post`, `reply`, `react`. Design questions to settle first:
whether `update` and `delete` belong in the same binary as `post` given how different
the trust levels are, and how the confirmation requirement holds up under an unattended
agent.

### Phase 3.1: Packaging and distribution

**Status:** Not planned. Publication is decided: open source under MIT (see Public
release prep above).

What remains: the PyPI name, and how much the README has to explain about Slack app
setup.

**Distribution as a Claude Code plugin (Option C), and the bundling question.** The
`slackline` skill ships in-repo with a copy-based install. When publication is
decided, a plugin manifest on top of the same skill directory gives versioned,
in-place-updatable installs. Design question to settle then, deliberately: a plugin may
*bundle* a copy of the zero-dependency package and run it via `python3 -m` so users need
no separate install, but the binary must stay independently installable and the read and
write binaries separately shippable. Skills are instructions, not boundaries: the
executable boundary (PRD section 2, R3, D2) and per-agent whitelisting require that an
agent can be given the read binary without the write binary ever being on its disk, so
the skill must never become the owner of both.

**Before any publication**, each of these is replaced or consciously kept (Phase 1.0 gate,
item 14). Done in Public release prep: real colleague handles in fixtures, tests, and
docs are synthetic stand-ins; the owner's real user ID, team ID, team name, and
workspace URL in `tests/test_cmd_whoami.py` and the docs are placeholders; the real
group DM name and handles cited as evidence in D6 and the resolver docstrings are
replaced with stand-ins of the same shape. The owner's own handle is replaced too.

---

## Completed

### Slackline v0.1.2: version tracking, per-repo skill copies, CI, first release

**Status:** Complete, 2026-09-02. One version in four places with VERSION as the number
of record, test-enforced; the skill documents per-repo copies and where a binary-less
cloner goes; CI (tests only) pulled forward from Phase 2.0 Step 11; tagged `v0.1.2` on
GitHub with a skill-only zip attached.

### Slackline skill, v0.1.1

**Status:** Complete, 2026-09-01. PR #15, merged with full history after an independent review pass.

**Plan:** [slackline-skill.md](slackline-skill.md), one PR, branch `feat/slackline-skill`

A Claude Code skill named `slackline`, shipped in this repo: for any read operation
from Slack, agents use `slackline-read` via the skill, never any other method or MCP;
the skill takes precedence. Writes are explicitly out of scope until `slackline-write`
exists, at which point the skill covers both. Credential stance: expect
`SLACK_USER_TOKEN`, stop and tell the user when it is missing, never hunt. Install is
agnostic (in-repo for repo sessions, one `cp -r` for global), with a drift test tying
the skill's flags and commands to the CLIs' help. Version bumps to 0.1.1. Pulled ahead
of Phase 2.0 deliberately: the skill is what puts the tool in play for every agent.

### PHASE GATE: Read-Only MVP

Ran 2026-08-28 as an independent review: **PASS_WITH_FINDINGS**, report at
[phase-gate-read-only-mvp.md](../assessments/phase-gate-read-only-mvp.md); its fix list was
implemented in PRs #10 to #13. Re-ran 2026-08-30 after those merged:
**PASS_WITH_FINDINGS** again, report at
[phase-gate-read-only-mvp-rerun.md](../assessments/phase-gate-read-only-mvp-rerun.md). All
fourteen items of the first fix list are closed and the high finding is gone; two new
mediums keep the item open: the offline suite passes only because the gitignored `.env`
supplies a credential (invariant 10), and `collect` under `--all-pages` is unbounded on a
server that keeps returning a cursor with empty pages. Plan those two
before any Phase 2.0 feature; the gate closes on a PASS.

Specific things this gate must verify rather than assume:
- The method allowlist test actually fails when a write method is introduced.
- The token-scrubbing test actually fails when scrubbing is removed.
- No test passes by asserting broken behavior.
- No real message content or colleague name entered the repository via fixtures.
- Exit codes match D18 in practice, and a truncated read really does exit 0.

**Passed 2026-08-30, third run:** report at
[phase-gate-read-only-mvp-rerun-2.md](../assessments/phase-gate-read-only-mvp-rerun-2.md).
Suite green in four environments; the allowlist guard, the scrubbing tests, every D18
code, both exit-0 truncations, and both of the second run's mediums verified by doing.
Three lows and five nits were recorded and seed Phase 2.0 below.

### Gate re-run fixes

**Status:** Complete, 2026-08-30. PR #14, merged with full history after two independent review passes and a security review.

**Plan:** [gate-rerun-fixes.md](gate-rerun-fixes.md), one PR, branch `fix/gate-rerun`

Close the nine items from the Read-Only MVP gate re-run
([phase-gate-read-only-mvp-rerun.md](../assessments/phase-gate-read-only-mvp-rerun.md)).
The two mediums that keep the gate open: argument errors must exit 2 with no credential
anywhere, so the offline suite needs no secret; and `collect` gets a page ceiling. Then the
lows and nits: a symlinked audit path is refused, the cache root follows the audit
directory's rule, `--order newest` keeps replies under their root, the fake models
Slack's parent-plus-limit reply pages, one shared `page_record()`, and doc corrections.

### Gate fixes PR 4: documentation that did not move with the code

**Status:** Complete, 2026-08-30. PR #13, merged with full history after an independent review pass. All twenty steps of the gate fix plan are done.

**Plan:** [read-only-mvp-gate-fixes.md](read-only-mvp-gate-fixes.md), Steps 17 to 20, branch `docs/gate-followups`

Credential precedence as implemented, README status, architecture layout, stale
comments, the Phase 3.1 identifier list, and the close-out live check.

**Live results, 2026-08-30, read-only:** the opt-in smoke suite passed (whoami, closed
pipe, reply-ts redirect); 753 conversations fetched with `--include-archived`, 305 of them
archived and hidden by default; on a 32-message window, `--all-pages --max-messages 10`
then `--cursor` returned 10 + 10 with zero overlap, contiguous with a full read; a
`--threads full --all-pages` read showed 0 incomplete threads and 0 duplicate `ts`; thread
continuation on a 5-message thread was exact, with the observation that Slack returns the
parent in addition to `limit` replies (closed by the gate re-run fixes); the reply-ts
redirect fired and was announced; the audit log file was `0600`.

### Gate fixes PR 3: hardening

**Status:** Complete, 2026-08-30. PR #12, merged with full history after two independent review passes and a security review.

**Plan:** [read-only-mvp-gate-fixes.md](read-only-mvp-gate-fixes.md), Steps 10 to 16, branch `fix/hardening`

Audit log permissions, `Retry-After` cap, terminal control characters, `team_id`
validation, text-versus-blocks precedence, `thread` given a reply's ts, exit 130.
Touches the security surface, so the security review applies.

### Gate fixes PR 2: directory and resolver

**Status:** Complete, 2026-08-29. PR #11, merged with full history after two independent review passes.

**Plan:** [read-only-mvp-gate-fixes.md](read-only-mvp-gate-fixes.md), Steps 8 to 9, branch `fix/directory-resolver`

`channels --include-archived` actually shows archived conversations, and `resolve`
reports verified group DM member sets.

### Gate fixes PR 1: continuation, threads, multi-conversation reads

**Status:** Complete, 2026-08-29. PR #10, merged with full history after two independent review passes.

**Plan:** [read-only-mvp-gate-fixes.md](read-only-mvp-gate-fixes.md), Steps 1 to 7, branch `fix/continuation`

Closes the high finding from the Phase 1.0 gate
([phase-gate-read-only-mvp.md](../assessments/phase-gate-read-only-mvp.md)): continuation
after a `--max-messages` truncation, by sizing each request to the remaining budget so
the ceiling lands on a page boundary. Same loop, same PR: paginated thread replies,
`thread_broadcast` deduplication, `--cursor` on `thread`, and multi-conversation reads
as a supported feature.

### Phase 1.0: `slackline-read` MVP

**Goal:** A working, installable, strictly read-only CLI that an agent can use to pull a
readable transcript out of any conversation the user can see.

**Status:** Complete, 2026-08-28

**Plan:** [slackline-read-mvp.md](slackline-read-mvp.md)

**Key deliverables:**
- `slackline-read` on PATH via `uv tool install`, and runnable uninstalled via `python3 -m`
- Six commands: `whoami`, `channels`, `users`, `resolve`, `conversation`, `thread`
- Credential chain with environment variable first, no TouchID required (D4a)
- Method allowlist with a test that prevents widening it (D3)
- Renderer handling blocks, attachments, files, mentions, links, and subtypes (D9, D10)
- Member-set-verified group DM resolution (D6)
- Cached user and conversation directories (D7)
- Text, JSONL, JSON, and raw formats (D12)
- Offline fixture suite, plus an opt-in live smoke test
- README with agent-facing usage

**Definition of done:** the six success criteria in PRD section 9. All six verified
against the live workspace on 2026-08-28, read-only, using the installed executable:
identity confirmed; 398 conversations discovered with no IDs supplied; a three-person
group DM resolved to exactly one conversation by member set; a 30-day thread-expanded
transcript returned 205 messages with zero empty renderings, zero unresolved `<@Uxxx>`
mentions, and zero raw link markup; the trailing page record reported `has_more` and its
cursor continued into 75 further messages with no overlap; a `--max-messages 5` ceiling
reported `truncated_by` and still exited 0; and the audit log recorded ten invocations
with no message content and no token-shaped string.

**Known gap, since closed:** no CI at completion time; shipped in v0.1.2
(`.github/workflows/tests.yml`, the offline suite on Python 3.9 and 3.13).

---

Repository initialized 2026-08-27.
