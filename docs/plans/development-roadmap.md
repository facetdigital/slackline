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

### Read attachments, especially images

**Status:** Ready to implement. Next, ahead of Phase 2.0.

**Plan:** [read-attachments.md](read-attachments.md), one PR, branch `feat/read-attachments`

The token has no `files:read` scope, so a file shared in a DM or channel, such as a screenshot or a spreadsheet, cannot be fetched: `url_private_download` answers with Slack's HTML login page. Today a file appears only as a rendered line like `[file: image.png (PNG, 261.0KB)]` inside the message text, in both text and `jsonl` output. Nothing is dropped, but there is no structured file metadata (ID, mime type, size) and no way to get the bytes; only `--format raw` carries the download URL.

Decided in planning, detail in the plan:
- A `files` command saves the attachments of one message, selected by conversation and `--ts` or by a message permalink, into a directory the caller names. `--save-files DIR` on `conversation` and `thread` does the same while reading, bounded by `--max-files`. Every attachment yields a record, saved with its path or skipped with a reason.
- JSONL message records gain a `files` array (ID, name, mime type, size, and more), and the text transcript shows each file's ID.
- The D3 method allowlist does not change: a file is found through its message, never by `files.info`. A download is guarded separately (new D25): the token goes only to `files.slack.com` over HTTPS, redirects are checked hop by hop, and only `api.py` may open a network connection.
- Files are saved as `<FILE_ID>-<cleaned name>`, owner-only, moved into place atomically, never outside the output directory, and capped by `--max-bytes`. Files Slack does not host, deleted files, and oversized files are skipped with a reason (new D26).
- The audit log records each download's file ID, mime type, and size, never its name or path (invariant 9).

Setup change: the manifest gains `files:read`, so existing users add the scope to their Slack app and reinstall it. Until they do, downloads fail with exit 3 and a message saying so.

Deferred: OCR and any text extraction from images or documents. This feature saves the bytes and reports where they are; what reads them comes later.

### Phase 2.0: Hardening and ergonomics

**Status:** Ready to implement

**Plan:** [phase-2-0-hardening-and-ergonomics.md](phase-2-0-hardening-and-ergonomics.md),
four PRs: gate seeds, permalinks (constructed locally, D23), policy files (exit 8, D24),
completions and CI.

- `--include-permalinks` via `chat.getPermalink`, which is read-only despite the `chat.`
  prefix. Worth confirming during planning rather than assuming from the name.
- Multiple conversations in one invocation: shipped before v0.2, with a header and page
  record per conversation. Any further framing polish lands here.
- `--policy` file support: allowlist and denylist of conversation IDs, plus ceilings, as a
  second layer under the executable boundary.
- Shell completions.
- CI: shipped early in v0.1.2 (`.github/workflows/tests.yml`); Step 11 of this phase's
  plan only verifies the checks appear and pass on this phase's own PRs.
- Slack Connect people cost one `users.info` each on a cold cache, which can be a hundred
  or more calls in a heavily shared workspace. Correct, and cached for 24h afterwards, but
  worth batching or deferring. Not blocking.
- Bidi override characters (U+202A..U+202E, U+2066..U+2069) are not stripped from text
  output. Reply expansion under `--all-pages` is bounded per thread by the page ceiling,
  not in aggregate.

Seeds from the Read-Only MVP phase gate, all low or nit. The plan cites them as `Seed-N`:
1. `cache.py`: bring `_ensure_root()` under the same degrade-to-warning rule as the write,
   so an unwritable, file-shadowed, or dangling-symlink `SLACKLINE_CACHE_DIR` warns and
   continues instead of exiting 1.
2. `directory.py`: do not cache an index whose final page still carried a cursor after
   `api.paginate` stopped at its ceiling, or store an `incomplete` flag and warn on load.
3. Trim the follow-ups bullet above to what is still open. Done.
4. `transcript.py`: the `Collected.truncated_by` comment should include `"max-pages"`.
5. Replace the `3 * constant` loop guards in the command and api tests with a fixed literal.
6. `docs/reference.md` "Reading the output": one sentence on the `max-pages` stop and its stderr line.
7. Extend the help-text test to require `parse` and the three-argument `run`.

---

## Upcoming

### Phase 2.1: Search (maybe)

**Status:** Not planned. Build it only if users ask.

A read-only `slackline-read search` command over `search.messages`. It fits the
read-only allowlist, but it needs the `search:read` scope (added to the Slack app and
reinstalled, which only the app's owner can do), and nothing has needed it yet: channel
discovery plus bounded reads have covered every real case. Search results are
relevance-ranked and cross-channel, a genuinely different contract from `conversation`
that deserves its own design rather than being bolted onto it.

### Phase 2.2: Which conversations have activity since T (idea)

**Status:** Not planned. Captured so it is not lost; build only when asked.

A caller who wants "anything new in any DM or group DM since T", without naming people,
today has to read every `im` and `mpim` conversation. On a workspace with a few hundred
of them that is one `conversations.history` call each, which takes minutes, and longer
when run again straight away, once Slack's per-method rate limit engages. A read-only way to ask which conversations have activity since T first, then read
only those, would turn that into seconds.

Design question to settle first: which read-only method can answer it in a few calls.
`search.messages` with a date filter is the obvious candidate and would make this ride on
Phase 2.1 and its `search:read` scope. Anything per-conversation keeps the N-calls cost
this exists to remove. Whatever it is must stay on the D3 allowlist.

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

**Status:** Not planned. The project is public under MIT and installs from GitHub
releases. What remains is a PyPI package, whose name is not decided yet.

**Distribution as a Claude Code plugin, and the bundling question.** The `slackline`
skill ships in-repo with a copy-based install. A plugin manifest on top of the same
skill directory would give versioned, in-place-updatable installs. Design question to
settle first, deliberately: a plugin may
*bundle* a copy of the zero-dependency package and run it via `python3 -m` so users need
no separate install, but the binary must stay independently installable and the read and
write binaries separately shippable. Skills are instructions, not boundaries: the
executable boundary (PRD section 2, R3, D2) and per-agent whitelisting require that an
agent can be given the read binary without the write binary ever being on its disk, so
the skill must never become the owner of both.

---

## Completed

### Go public

**Status:** Complete, 2026-10-25. The repository is public, with v0.2.2 as its first public release.

Before the flip, a colleague followed the quick start cold. The install worked end to end, from a clone, through asking Claude about a DM. The notes were about the quick start: its Slack app steps no longer matched Slack's screens, it did not show what `whoami` should print, it read as if 1Password were the only way to hand an agent the token, and there were no uninstall steps. The README was rewritten from those notes, and its reference detail moved to `docs/credentials.md` and `docs/reference.md`. A non-admin install was tried in other workspaces: some allow it and some require an admin to approve the app, so the quick start says what a refusal looks like and how to request the install. A scan of the full git history before the flip found only synthetic test tokens.

Right after the flip, fork pull requests were set to need approval for every outside contributor, and private vulnerability reporting was turned on. The fork setting took only with a JSON body; the same call with `-f approval_policy=...` returned 204 and left the old value:

```bash
echo '{"approval_policy":"all_external_contributors"}' | gh api -X PUT \
  repos/facetdigital/slackline/actions/permissions/fork-pr-contributor-approval --input -
gh api -X PUT repos/facetdigital/slackline/private-vulnerability-reporting
```

### Slackline v0.2.2: the docs a newcomer reads

**Status:** Complete, 2026-10-25. Tagged `v0.2.2`, with the wheel, the source tarball, and the skill zip attached.

No code changes. Cut so the first public release carries the rewritten README and a skill whose relaunch hint offers the plain `read -rs` path before 1Password. Without it, the release's skill zip and source tarball would have contradicted the README on main.

### Slackline v0.2.1: fixes from the manual test pass

**Status:** Complete, 2026-10-16. Tagged `v0.2.1`, with the wheel, the source tarball, and the skill zip attached.

Batches the code changes made since v0.2.0, all found by walking through the release as a newcomer would: archived conversations are marked `(archived)` in the text channel list, and a bare name in `--with` is a name search rather than a handle (below). Docs from the same pass: a run-without-installing command that works, an upgrade command that rebuilds (`--force --reinstall`), and installing the skill from a release zip.

### A bare name is a name, not a handle

**Status:** Complete, 2026-10-16.

Found in the v0.2.0 manual test pass: `--with Kevin` silently resolved to whoever held the handle `kevin` while a second Kevin existed, the guess D8 forbids. An exact handle now wins only when the selector starts with `@`; a bare word searches handles, real names, and display names together, and two matches are exit 4 with the candidates, whose message names `@handle` as the way to pick one. An `@` selector that is nobody's handle falls back to the same search. `users --match joan` lists every Joan for the same reason. Recorded under D8 in the architecture doc.

### Slackline v0.2.0: open-source release prep

**Status:** Complete, 2026-10-01. Tagged `v0.2.0` with a skill-only zip attached. The public history starts here: the repository was recreated with one squashed commit for the open-source release.

*Slackline* is the project; *slackline-read* is the executable that exists today; *slackline-write* will someday be a second, separate executable in the same project, never a mode or flag inside slackline-read. Slackline is not meant to be a full MCP replacement or a full API wrapper. Being read-only by construction is the product, and anything that weakens that promise is out of scope for slackline-read.

Delivered: the MIT license and matching `pyproject.toml` metadata; Slack Connect people rendered by name and marked `(ext)` instead of raw IDs (the message's `user_profile` first, then a cached `users.info`, then the ID); a "What this is not" section in the README; and a publication check of the whole history, after which real names, IDs, and workspace details in fixtures, tests, and docs were replaced with synthetic stand-ins of the same shape.

### Slackline v0.1.2: version tracking, per-repo skill copies, CI

**Status:** Complete, 2026-09-02. One version in four places with `VERSION` as the number of record, test-enforced; the skill documents per-repo copies and where someone without the binary goes; CI running the offline suite on Python 3.9 and 3.13.

### Slackline skill, v0.1.1

**Status:** Complete, 2026-09-01.

A Claude Code skill named `slackline`, shipped in this repo: for any read operation from Slack, agents use `slackline-read` via the skill, never any other method or MCP; the skill takes precedence. Writes are out of scope until `slackline-write` exists, at which point the skill covers both. Credential stance: expect `SLACK_USER_TOKEN`, stop and tell the user when it is missing, never hunt for one. Install is agnostic (in-repo for repo sessions, one `cp -r` for global), with a drift test tying the skill's flags and commands to the CLI's help. Decided against: putting the binary inside the skill directory, because a skill is instructions, not a boundary (D2).

### Read-Only MVP phase gate and its fixes

**Status:** Passed, 2026-08-30, on the third run.

An independent audit of the MVP, which had to verify rather than assume: that the method allowlist test fails when a write method is introduced, that the token-scrubbing test fails when scrubbing is removed, that no test passes by asserting broken behavior, that no real message content or colleague name entered the repository through fixtures, and that exit codes match D18, including exit 0 on a truncated read.

The first two runs passed with findings, and the fixes landed before v0.1.1:
- Exact continuation after a `--max-messages` truncation, by sizing each request to the remaining budget so the ceiling lands on a page boundary; paginated thread replies; `thread_broadcast` deduplication; `--cursor` on `thread`; and several conversations per invocation as a supported feature.
- `channels --include-archived` shows archived conversations, and `resolve` reports verified group DM member sets.
- Hardening: audit log permissions, a `Retry-After` cap, terminal control characters stripped, `team_id` validation, text-versus-blocks precedence, `thread` accepting a reply's ts, exit 130 on interrupt.
- Argument errors exit 2 before any credential is resolved, so the offline suite needs no secret; a page ceiling on `collect`; a symlinked audit path is refused; `--order newest` keeps replies under their root; one shared `page_record()`.
- Documentation brought back in line with the code.

A live check after the fixes, read-only: continuation after a truncation returned the next page with zero overlap and no gap; a `--threads full --all-pages` read showed no incomplete threads and no duplicate `ts`; thread continuation was exact, with the observation that Slack returns the parent in addition to `limit` replies (since modeled in the test fake); the audit log file was `0600`.

The third run passed with three lows and five nits, which seed Phase 2.0 above.

### Phase 1.0: `slackline-read` MVP

**Goal:** A working, installable, strictly read-only CLI that an agent can use to pull a readable transcript out of any conversation the user can see.

**Status:** Complete, 2026-08-28

**Key deliverables:**
- `slackline-read` on PATH via `uv tool install`, and runnable uninstalled via `python3 -m`
- Six commands: `whoami`, `channels`, `users`, `resolve`, `conversation`, `thread`
- Credential chain with the environment variable first, no TouchID required (D4a)
- Method allowlist with a test that prevents widening it (D3)
- Renderer handling blocks, attachments, files, mentions, links, and subtypes (D9, D10)
- Member-set-verified group DM resolution (D6)
- Cached user and conversation directories (D7)
- Text, JSONL, JSON, and raw formats (D12)
- Offline fixture suite, plus an opt-in live smoke test
- README with agent-facing usage

**Definition of done:** the six success criteria in PRD section 9, all verified against a real workspace on 2026-08-28, read-only, using the installed executable: identity confirmed; every accessible conversation discovered with no IDs supplied; a three-person group DM resolved to exactly one conversation by member set; a 30-day thread-expanded transcript with zero empty renderings, zero unresolved `<@Uxxx>` mentions, and zero raw link markup; the trailing page record reported `has_more` and its cursor continued with no overlap; a `--max-messages 5` ceiling reported `truncated_by` and still exited 0; and the audit log recorded every invocation with no message content and no token-shaped string.

---

Repository initialized 2026-08-27.
