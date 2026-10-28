# Plan: Phase 2.0, Hardening and Ergonomics

## Execution Instructions

When executing this plan:

1. **Work step-by-step.** Complete each step fully before moving to the next.
2. **Test-first within each step.** Write the failing test before the implementation, run
   it, watch it fail for the reason the step describes, then implement until it passes.
   Never edit the test afterwards to make it pass. If a step's test passes before the
   implementation, stop: either the test is wrong or the defect is not what the step says.
3. **Test after each step.** Run the commands under **Test:** and confirm green. Check
   pytest's own exit status, never a piped tail's.
4. **Commit after each step**, using the given commit message. The body says why.
5. **Update documentation continuously.** README, CLAUDE.md, the architecture doc when a
   decision changes, this plan's checkboxes, and the roadmap move in the same PR.
6. **Group steps into PRs as marked.** One branch per PR, branched from `main`, merged
   with `--no-ff`. Never squash, never rebase.
7. **Mark completion.** When every step is done, move Phase 2.0 to Completed in the
   roadmap and promote the `PHASE GATE: Phase 2.0` marker (Step 14 adds it) so the gate
   runs before Phase 2.1 or 3.0 starts.

Conventions: run the suite as `.venv/bin/python -m pytest -q`; cite decision numbers in
comments; never use an emdash in any text; fixtures stay synthetic; `FakeClient` in
`tests/conftest.py` enforces the read-only allowlist; no runtime dependency (D1); the
offline suite must stay green from an empty directory with no credential (invariant 10).

**Read-only stays enforced.** Nothing in this phase adds a Slack method. The permalink
feature is designed specifically so the `chat.*` family stays banned wholesale (Step 5).

**Legend:** `Dn` cites [architecture.md](../architecture/architecture.md). `Pn` cites
[product-requirements.md](../requirements/product-requirements.md). `Seed-N` cites the
numbered seed list in the roadmap's Phase 2.0 entry, from the Read-Only MVP phase gate.

---

## Summary

Phase 2.0 hardens the read-only MVP and rounds out its ergonomics, in four PRs:

1. The seven low/nit seeds from the passed Read-Only MVP gate.
2. `--include-permalinks`: permalinks constructed locally from `auth.test`'s workspace
   URL, filling the `permalink` field D13 has promised since the MVP, with zero extra
   API calls and no new Slack method (new decision D23).
3. `--policy` files: an allowlist and denylist of conversation IDs plus a message
   ceiling, a second refusal layer under the executable boundary, with new exit code 8
   (new decision D24, D18 amendment).
4. A `completions` command for bash and zsh, and CI running the offline suite on every
   PR (unblocked: the suite needs no credential since the gate fixes).

Phase 2.1 (search) stays out: it needs the `search:read` scope and is built only if users ask.

## Requirements

- Cache and directory failures degrade to warnings, and a truncated directory is never
  cached as complete (D5, D7, Seed-1, Seed-2)
- Test guards use fixed literals; the help-text test pins the full command protocol
  (Seed-5, Seed-7)
- Docs corrected: `Collected.truncated_by` comment, `docs/reference.md` `max-pages` sentence, roadmap
  follow-ups bullet (Seed-3, Seed-4, Seed-6)
- `--include-permalinks` on `conversation` and `thread`; the `permalink` key is always
  present in message records, null unless requested (D13, D23-new, P7)
- `--policy PATH` / `SLACKLINE_POLICY`: deny wins, a non-empty allow list is exclusive,
  a policy message ceiling caps `--max-messages`; refusal is exit 8, whole-invocation,
  before anything is read (D24-new, D18, P10's spirit: never a silent subset)
- `resolve` annotates matches with the policy decision instead of refusing, since it is
  the inspection tool (D8)
- `completions bash|zsh` prints a completion script, needs no credential, and cannot
  drift from the documented flags (D19)
- CI runs the offline suite on every PR and on main, Python 3.9 and 3.13 (D21)
- Everything stays read-only: `READ_ONLY_METHODS` is untouched by this phase (R1, D3)

---

## PR 1: Gate seeds

Branch: `fix/gate-pass-seeds`

### Step 1: `_ensure_root` degrades to a warning

The gate found three shapes of `SLACKLINE_CACHE_DIR` that raise out of `_ensure_root`
to exit 1: a dangling symlink at the root (`os.mkdir` raises `FileExistsError`, then
`os.stat` follows the link and raises `FileNotFoundError`), an unwritable parent
(`mkdir(parents=True)` raises `PermissionError`), and a plain file at the root's name.
A cache that cannot exist is a slow run, not a failed one.

- [ ] Write the failing tests first: `tests/test_cache.py`
  - `test_a_dangling_symlink_cache_root_degrades_to_a_warning`: root is a symlink to a
    path that does not exist; `Cache(root=..., warn=...).put("users", {})` raises
    nothing; a warning names the root; `get` afterwards returns `None`.
  - `test_an_unwritable_parent_degrades_to_a_warning`: parent directory chmod `0o500`;
    root beneath it; `put` warns, does not raise; restore the mode in `finally`.
  - `test_a_file_at_the_cache_root_name_degrades_to_a_warning`: a regular file where the
    root should be; `put` warns, does not raise.
  Fails: each raises out of `put` today.
- [ ] Implement in `cache.py`: wrap the body of `_ensure_root` in `try/except OSError`,
  route to `self.warn(f"cannot use cache directory {self.root}: {exc}")` once per
  instance (reuse `_warned_about_root`), and have `put` return without writing when the
  root is unusable (`_ensure_root` returns a bool, or sets a flag `put` checks).
  Comment: the same degrade-to-warning rule as the write side, per the gate (Seed-1).
- [ ] Verify green.

**Satisfies:** D7; Seed-1

**File(s):** `src/slackline/cache.py`, `tests/test_cache.py`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_cache.py -q
```

**Commit message:** `Degrade every unusable cache root to a warning instead of exit 1`

---

### Step 2: A truncated directory is never cached as complete

`api.paginate` now stops at `MAX_PAGES_PER_CALL` and announces it, but
`Directory.users()` and `conversations()` would cache whatever arrived as if it were
the whole workspace, and every later invocation inside the TTL would trust it silently
(invariant 5: a subset must never look like the whole).

- [ ] Write the failing tests first: `tests/test_directory.py`
  - `test_a_truncated_user_index_is_not_cached`: a `users.list` fixture whose final page
    carries a non-empty `next_cursor` (that is what a ceiling-stopped `paginate`
    iteration looks like to the consumer: the loop ends with the last page's cursor
    still set). Build the directory; assert the in-memory index contains what arrived,
    `cache.get("users", ...)` is `None`, and a warning containing `"incomplete"` was
    emitted through `cache.warn`.
  - `test_a_truncated_conversation_inventory_is_not_cached`: same shape for
    `users.conversations`.
  - `test_a_complete_directory_is_still_cached`: the existing fixtures round-trip as
    before (pin).
  Fails: both are cached today and nothing warns.
- [ ] Implement in `directory.py`: in both loops, remember the last `page.next_cursor`;
  after the loop, if it is non-empty, keep the in-memory index (`self._users` /
  `self._conversations`), skip `cache.put`, and warn via `self.cache.warn`:
  `"the user directory is incomplete (the page ceiling stopped the fetch); using what
  arrived and not caching it"`. Same pattern in `resolve.py:_members_of`: skip the
  member-cache store for a membership list whose final page still had a cursor.
- [ ] Verify green.

**Satisfies:** D5, D7, CLAUDE.md invariant 5; Seed-2

**File(s):** `src/slackline/directory.py`, `src/slackline/resolve.py`, `tests/test_directory.py`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_directory.py tests/test_resolve.py -q
```

**Commit message:** `Never cache a directory the page ceiling truncated`

---

### Step 3: Test guards and the command-protocol pin

- [ ] Test-first: these steps change tests, and the tests are the deliverable.
  - Replace `3 * transcript.MAX_PAGES` in `tests/test_cmd_conversation.py:601` and
    `tests/test_cmd_thread.py:480`, and `api.MAX_PAGES_PER_CALL * 3` in
    `tests/test_api.py:259`, with the literal `300` (matching `GUARD = 300` in
    `tests/test_transcript_collect.py`), so a removed ceiling fails the test rather
    than scaling the guard away with it.
  - Extend `test_every_command_exposes_the_help_text_dispatch_relies_on` in
    `tests/test_review_regressions.py`: every command module must also expose a callable
    `parse` and a `run` whose signature accepts `(ctx, argv, options)` (use
    `inspect.signature(module.run)`; three parameters, the third defaulted).
  - Confirm by hand that a module stripped of `parse` fails it (revert; do not commit).
- [ ] Verify green.

**Satisfies:** D15's ceilings stay load-bearing; CLAUDE.md commands layout row; Seed-5, Seed-7

**File(s):** `tests/test_cmd_conversation.py`, `tests/test_cmd_thread.py`, `tests/test_api.py`, `tests/test_review_regressions.py`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_api.py tests/test_review_regressions.py tests/test_cmd_conversation.py tests/test_cmd_thread.py -q
```

**Commit message:** `Pin the ceilings with literals and the command protocol with the help test`

---

### Step 4: Doc corrections from the gate

- [ ] Test-first: n/a, documentation. Verification is the grep below.
- [ ] `src/slackline/transcript.py:127`: the `Collected.truncated_by` field comment
  becomes `# "max-messages", "max-pages", or None`.
- [ ] `docs/reference.md`, "Reading the output": after the truncation paragraph, one sentence:
  a fetch also stops after 100 pages (`truncated_by: "max-pages"`), announced on
  stderr, with the cursor still usable.
- [x] `docs/plans/development-roadmap.md`, the follow-ups bullet: remove the
  `page_record()`, `collect` page ceiling, and FIFO items the gate fixes closed; add
  "reply expansion is bounded per thread by the same page ceiling"; keep the
  bidi-override item. Done in the pre-launch docs cleanup.
- [ ] Verify: `grep -n '"max-messages", or None' src/slackline/transcript.py` returns
  nothing; `grep -c "max-pages" docs/reference.md` is at least 1;
  `grep -n "page_record()" docs/plans/development-roadmap.md` returns nothing;
  suite green.

**Satisfies:** D15, D16; roadmap "Docs move with the code"; Seed-3, Seed-4, Seed-6

**File(s):** `src/slackline/transcript.py`, `docs/reference.md`, `docs/plans/development-roadmap.md`

**Test:**
```bash
grep -n '"max-messages", or None' src/slackline/transcript.py; grep -n "page_record()" docs/plans/development-roadmap.md
.venv/bin/python -m pytest -q
```

**Commit message:** `Close the documentation seeds from the passed gate`

---

## PR 2: Permalinks

Branch: `feat/permalinks`

### Step 5: Local permalink construction (new decision D23)

Slack permalinks have the shape
`https://<workspace>.slack.com/archives/<CONVERSATION_ID>/p<ts with the dot removed>`,
and a thread reply adds `?thread_ts=<root ts>&cid=<CONVERSATION_ID>`. Every part is
already in hand: the workspace URL from `auth.test` (cached on `Context.identity()`),
the conversation ID, and the message `ts`. Constructing locally costs zero API calls
for any transcript size and keeps the `chat.*` family banned wholesale;
`chat.getPermalink` would buy only Enterprise Grid domain quirks this workspace does
not have. That reasoning becomes D23.

- [ ] Write the failing tests first: `tests/test_transcript_permalink.py` (new)
  - `test_a_top_level_message_permalink`: `permalink("https://example.slack.com/",
    "C123", "1756310012.000100")` is
    `"https://example.slack.com/archives/C123/p1756310012000100"`.
  - `test_a_reply_permalink_carries_the_thread`: with `thread_ts="1756310000.000100"`,
    the result ends `?thread_ts=1756310000.000100&cid=C123`.
  - `test_a_root_is_not_treated_as_a_reply`: `thread_ts` equal to `ts` adds no query.
  - `test_missing_pieces_yield_none`: empty URL, empty ID, or unparseable ts returns
    `None` rather than a broken link.
  - `test_the_url_join_survives_a_trailing_slash_or_none`.
  Fails: no function.
- [ ] Implement `permalink(team_url, conversation_id, ts, thread_ts=None)` in
  `src/slackline/transcript.py` (it is part of the message-record contract). Validate
  `ts` against `^\d+\.\d+$` before transforming; return `None` on anything missing.
- [ ] `docs/architecture/architecture.md`: add **D23. Permalinks are constructed
  locally, not fetched**, with the shape, the reasoning above, and the explicit note
  that `chat.getPermalink` was considered and rejected so `chat.*` stays banned
  (the guard test `test_the_allowlist_contains_no_mutating_method` keeps asserting the
  whole family is absent, unchanged).
- [ ] Verify green.

**Satisfies:** D3, D13, D23-new; roadmap Phase 2.0 permalink bullet

**File(s):** `src/slackline/transcript.py`, `tests/test_transcript_permalink.py`, `docs/architecture/architecture.md`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_transcript_permalink.py tests/test_readonly_guard.py -q
```

**Commit message:** `Construct permalinks locally so chat.* stays banned (D23)`

---

### Step 6: `--include-permalinks` on `conversation` and `thread`

- [ ] Write the failing tests first:
  - `tests/test_cmd_conversation.py`: `test_the_permalink_key_is_always_present_and_null_by_default`
    (D13 has promised the key since the MVP; every message record carries
    `"permalink": null` without the flag);
    `test_include_permalinks_fills_the_key`: with `--include-permalinks` (and the
    `ctx._identity` fixture given a `url`), every message record's `permalink` starts
    with the workspace URL and contains the conversation ID; a reply's contains
    `thread_ts=`.
  - `tests/test_cmd_thread.py`: same pair for `thread`.
  - `tests/test_formatters_text.py`: `test_the_text_transcript_shows_a_permalink_when_present`:
    a message record with a permalink renders it as an indented trailer line; absent or
    null renders nothing extra.
  Fails: the key does not exist and the flag is unknown (exit 2).
- [ ] Implement:
  - `transcript.message_record` gains `permalink: Optional[str] = None` and always emits
    the key (null when not computed), closing the D13 sample/code mismatch.
  - `conversation.py` and `thread.py`: `parse` accepts `--include-permalinks`
    (HELP updated); `run` computes `base = ctx.identity().get("url")` once when the
    flag is on and passes `permalink=permalink(base, conversation_id, ts, thread_ts)`
    per message. `whoami` already surfaces the same `url`.
  - `formatters/text.py` `_message`: when `record.get("permalink")`, append
    `f"{indent}  {permalink}"` after the text lines (cleaned by the existing seam).
- [ ] `README.md`: the flag under `conversation` and `thread`, one sentence that links
  are built locally and cost no extra calls. Delete the `--include-permalinks` row from
  "What is not built yet".
- [ ] Verify green.

**Satisfies:** D13, D19, D23, P7; roadmap Phase 2.0 permalink bullet

**File(s):** `src/slackline/transcript.py`, `src/slackline/commands/conversation.py`, `src/slackline/commands/thread.py`, `src/slackline/formatters/text.py`, `README.md`, `tests/test_cmd_conversation.py`, `tests/test_cmd_thread.py`, `tests/test_formatters_text.py`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_cmd_conversation.py tests/test_cmd_thread.py tests/test_formatters_text.py -q
```

**Commit message:** `Add --include-permalinks, filling the field D13 promised, at zero API cost`

---

## PR 3: Policy files

Branch: `feat/policy`

### Step 7: Exit code 8 and the policy error (D18 amendment)

- [ ] Write the failing tests first: `tests/test_errors.py`
  - `test_policy_refusals_carry_exit_code_eight`: `errors.PolicyRefused("...").exit_code == 8`.
  - The existing `test_exit_code_six_is_unassigned` keeps passing unmodified (8 is new;
    6 stays unclaimed).
  - `test_the_help_text_documents_exit_eight`: `"8" in cli_read.EXIT_CODES` on a line
    containing `"policy"`.
  Fails: no class, no help line.
- [ ] Implement `PolicyRefused(SlacklineError)` with `exit_code = 8` in `errors.py`
  (docstring: a local configuration refused the read; distinct from 5, where Slack
  said no, and from 7, where the binary itself cannot). Add `    8  refused by the
  --policy file` to `EXIT_CODES`, the D18 table, and the exit-code table in `docs/reference.md`.
- [ ] Verify green.

**Satisfies:** D18, P12; roadmap Phase 2.0 policy bullet

**File(s):** `src/slackline/errors.py`, `src/slackline/cli_read.py`, `docs/architecture/architecture.md`, `docs/reference.md`, `tests/test_errors.py`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_errors.py -q
```

**Commit message:** `Assign exit 8 to policy refusals (D18)`

---

### Step 8: The policy file (new decision D24)

Format, JSON, zero dependencies:

```json
{"allow": ["C123", "G456"], "deny": ["D789"], "max_messages": 200}
```

Rules: `deny` always wins; a non-empty `allow` is exclusive (anything not listed is
refused); both lists hold conversation IDs only, never names, because names are neither
unique nor stable (D6) and a policy that names things would be a policy that guesses;
`max_messages` caps the effective ceiling (`min(policy, flag)`). A malformed file, an
unreadable path, or an unknown key is exit 2: a policy that cannot be read must never
fail open.

- [ ] Write the failing tests first: `tests/test_policy.py` (new)
  - load: happy path; missing file is exit-2 `ArgumentError`; malformed JSON is exit 2;
    an unknown key is exit 2 (a typo like `"alow"` must not silently allow everything);
    non-string IDs are exit 2; empty file `{}` is a valid no-op policy.
  - decisions: deny wins over allow; non-empty allow excludes an unlisted ID; empty
    allow list plus empty deny allows everything; `max_messages` caps a larger flag
    value and leaves a smaller one alone.
  Fails: no module.
- [ ] Implement `src/slackline/policy.py`: `load(path) -> Policy` and
  `Policy.check(conversation_id) -> Optional[str]` (a refusal reason or `None`),
  `Policy.cap(max_messages) -> int`. Resolution: `--policy PATH` global flag, else
  `SLACKLINE_POLICY` env var, else no policy.
- [ ] `docs/architecture/architecture.md`: **D24. Policy files are a second refusal
  layer under the executable boundary**: IDs only and why, deny wins, allow is
  exclusive, fails closed on any parse doubt, exit 8, and enforcement happens before
  any message is read.
- [ ] Verify green.

**Satisfies:** D24-new, D6 (IDs, never names), P13; roadmap Phase 2.0 policy bullet

**File(s):** `src/slackline/policy.py`, `tests/test_policy.py`, `docs/architecture/architecture.md`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_policy.py -q
```

**Commit message:** `Add the policy file: IDs only, deny wins, fails closed (D24)`

---

### Step 9: Enforcement in the commands

- [ ] Write the failing tests first:
  - `tests/test_cmd_conversation.py`: with a policy denying the resolved conversation,
    `conversation` raises `PolicyRefused` before any `conversations.history` call
    (assert on `ctx.client.calls`); with several conversations where one is denied,
    the whole invocation is refused and nothing is read (never a silent subset,
    invariant 5); a policy `max_messages` of 1 truncates like `--max-messages 1`.
  - `tests/test_cmd_thread.py`: a denied conversation is refused before
    `conversations.replies`.
  - `tests/test_cmd_resolve.py`: `resolve` does NOT refuse; with a policy active, each
    match carries `"policy": "allowed"` or `"policy": "denied"`, and without a policy
    the key is absent. `resolve` is the inspection tool; hiding candidates there would
    make exit 8 undiagnosable (D8).
  - `tests/test_cli_argument_errors.py`: `--policy /nonexistent` exits 2 with no
    credential (policy loading belongs to `parse`-time, before the token).
  Fails: the flag is unknown.
- [ ] Implement:
  - `cli_read._parse_global_flags`: `--policy PATH`; `Context` carries the loaded
    policy; loading happens in `_dispatch` right after `command.parse(rest)` and
    before `resolve_token` (a malformed policy is an argument-class failure and needs
    no credential).
  - `conversation.py` and `thread.py`: after resolution, check every selected
    conversation; on any refusal raise `PolicyRefused` naming the ID, the reason, and
    the policy source (flag path or env var). Apply `policy.cap` to
    `options["max_messages"]` before collecting.
  - `commands/resolve.py`: annotate matches when a policy is active.
  - Audit log: nothing new needed; argv already records `--policy` and the exit code 8
    lands in the existing field. Confirm with one assertion in `tests/test_audit.py`.
- [ ] `README.md`: a "Policy files" section with the JSON example, the rules, exit 8,
  and one sentence on the layering: the executable boundary says what the binary can
  ever do, the policy says what this invocation may read. `.env.example`: document
  `SLACKLINE_POLICY`. `CLAUDE.md`: add the policy layer to the invariants list
  (enforced before any read; fails closed).
- [ ] Verify green, and the suite from an empty directory.

**Satisfies:** D8, D18, D24, P10, P13, CLAUDE.md invariant 5; roadmap Phase 2.0 policy bullet

**File(s):** `src/slackline/cli_read.py`, `src/slackline/commands/conversation.py`, `src/slackline/commands/thread.py`, `src/slackline/commands/resolve.py`, `README.md`, `.env.example`, `CLAUDE.md`, `tests/test_cmd_conversation.py`, `tests/test_cmd_thread.py`, `tests/test_cmd_resolve.py`, `tests/test_cli_argument_errors.py`, `tests/test_audit.py`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_cmd_conversation.py tests/test_cmd_thread.py tests/test_cmd_resolve.py tests/test_cli_argument_errors.py tests/test_audit.py -q
.venv/bin/python -m pytest -q
```

**Commit message:** `Enforce the policy before anything is read, whole invocation or nothing`

---

## PR 4: Completions and CI

Branch: `feat/completions-ci`

### Step 10: The `completions` command, credential-free

- [ ] Write the failing tests first: `tests/test_cmd_completions.py` (new)
  - `test_bash_and_zsh_scripts_are_printed`: `completions bash` and `completions zsh`
    exit 0 and print a non-empty script mentioning `slackline-read`.
  - `test_an_unknown_shell_is_an_argument_error` (exit 2, names the two options).
  - `test_no_credential_is_needed`: through `cli_read.main(["completions", "bash"])`
    from an empty directory with no token: exit 0 (the conftest guard enforces that no
    credential helper ran).
  - `test_the_script_knows_every_documented_flag`: for each command module, every
    `--flag` token appearing in its `HELP` is present in the bash script (a drift test:
    a new flag without a completion fails here).
  - `test_stdout_carries_only_the_script` (D19): stderr empty on success.
- [ ] Implement `src/slackline/commands/completions.py`: `HELP`, `parse` (shell name),
  `run` printing a static template filled from `cli_read.COMMANDS` and each module's
  `HELP`-declared flags (extract with a regex over HELP at generation time, so the
  script cannot drift). Module attribute `NEEDS_CLIENT = False`.
- [ ] Implement in `cli_read._dispatch`: when
  `getattr(command, "NEEDS_CLIENT", True)` is false, skip `resolve_token` and the
  `SlackClient` construction. Add `completions` to `COMMANDS` and the `USAGE` command
  list. The extended help-text test from Step 3 covers the new module automatically.
- [ ] `README.md`: an "Installation" subsection showing
  `slackline-read completions bash >> ~/.bash_completion` and the zsh equivalent.
  Delete the completions row from "What is not built yet".
- [ ] Verify green.

**Satisfies:** D19, P16 (nothing interactive, no credential); roadmap Phase 2.0 completions bullet

**File(s):** `src/slackline/commands/completions.py`, `src/slackline/cli_read.py`, `README.md`, `tests/test_cmd_completions.py`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_cmd_completions.py tests/test_review_regressions.py -q
```

**Commit message:** `Add a completions command that needs no credential and cannot drift`

---

### Step 11: CI on every PR

**Shipped early** with the v0.1.2 release PR, ahead of this plan, because the release
work wanted checks on its own PR. When executing this plan, verify the checks appear
and pass on this phase's PRs, then tick this step; the workflow file already exists.

- [ ] Test-first: n/a, infrastructure. Verification is the PR's own checks turning
  green, which the executing session must wait for and confirm before merge.
- [ ] Write `.github/workflows/tests.yml`: on `pull_request` and `push` to `main`;
  `ubuntu-latest`; matrix Python `3.9` and `3.13`; steps: checkout, setup-python,
  `pip install -e ".[dev]"`, `python -m pytest -q` (the offline suite: no secret
  exists in CI, which is exactly the point; `tests/live` is excluded by `addopts` and
  skips anyway). Pin action versions (`actions/checkout@v4`, `actions/setup-python@v5`).
- [ ] `README.md`: replace the "CI on every PR" row in "What is not built yet" and the
  "Known gap: no CI" note in the roadmap's Phase 1.0 entry with the reality.
- [ ] Verify: push the branch, open the PR, and confirm the new checks run and pass on
  the PR itself before merging (this PR is its own end-to-end test). From here on, the
  merge discipline includes waiting for CI green.
- [ ] Verify offline: `.venv/bin/python -c "import json; print('yaml not needed')"` is
  not a check; instead run the suite once more locally and confirm the workflow file
  contains no tab characters (`grep -P '\t' .github/workflows/tests.yml` empty).

**Satisfies:** D21 (fixtures, no secrets, runnable in CI); roadmap Phase 2.0 CI bullet; Phase 1.0 known gap

**File(s):** `.github/workflows/tests.yml`, `README.md`, `docs/plans/development-roadmap.md`

**Test:**
```bash
grep -c . .github/workflows/tests.yml
.venv/bin/python -m pytest -q
```

**Commit message:** `Run the offline suite in CI on every PR, now that it needs no secret`

---

### Step 12: Live verification and close-out

- [ ] Test-first: n/a.
- [ ] Live, read-only, opt-in: `--include-permalinks` on a real conversation and thread;
  open one produced permalink in a browser by hand and confirm it lands on the message
  (record the result, not the URL, in the PR description); a `--policy` file denying a
  real conversation exits 8 before any history call (verify via the audit log's
  conversation list staying empty for that run); `completions bash` sourced into a
  shell completes `slackline-read conv<TAB>`.
- [ ] Every checkbox in this plan ticked. Roadmap: move Phase 2.0 to Completed with the
  date and PR numbers; add a new `### PHASE GATE: Phase 2.0` item as the next
  immediate step, with the verify-not-assume list: exit 8 fires before any read; the
  policy fails closed on a malformed file; the permalink guard test still bans all of
  `chat.*`; CI is green on the PR that added it; the suite still needs no credential.
- [ ] Full suite green, plus the empty-directory run.

**Satisfies:** PRD section 9's spirit for the new surface; roadmap Phase 2.0 exit

**File(s):** `docs/plans/development-roadmap.md`, `docs/plans/phase-2-0-hardening-and-ergonomics.md`

**Test:**
```bash
.venv/bin/python -m pytest -q
```

**Commit message:** `Mark Phase 2.0 complete and stage its gate`

---

## Files Modified (Summary)

| File | Steps |
|------|-------|
| `src/slackline/cache.py` | 1 |
| `tests/test_cache.py` | 1 |
| `src/slackline/directory.py` | 2 |
| `src/slackline/resolve.py` | 2 |
| `tests/test_directory.py` | 2 |
| `tests/test_cmd_conversation.py` | 3, 6, 9 |
| `tests/test_cmd_thread.py` | 3, 6, 9 |
| `tests/test_api.py` | 3 |
| `tests/test_review_regressions.py` | 3 |
| `src/slackline/transcript.py` | 4, 5, 6 |
| `tests/test_transcript_permalink.py` | 5 |
| `src/slackline/commands/conversation.py` | 6, 9 |
| `src/slackline/commands/thread.py` | 6, 9 |
| `src/slackline/formatters/text.py` | 6 |
| `tests/test_formatters_text.py` | 6 |
| `src/slackline/errors.py` | 7 |
| `src/slackline/cli_read.py` | 7, 9, 10 |
| `src/slackline/policy.py` | 8 |
| `tests/test_policy.py` | 8 |
| `tests/test_errors.py` | 7 |
| `src/slackline/commands/resolve.py` | 9 |
| `tests/test_cmd_resolve.py` | 9 |
| `tests/test_cli_argument_errors.py` | 9 |
| `tests/test_audit.py` | 9 |
| `src/slackline/commands/completions.py` | 10 |
| `tests/test_cmd_completions.py` | 10 |
| `.github/workflows/tests.yml` | 11 |
| `.env.example` | 9 |
| `CLAUDE.md` | 9 |
| `README.md` | 6, 9, 10, 11 |
| `docs/reference.md` | 4, 7 |
| `docs/architecture/architecture.md` | 5, 7, 8 |
| `docs/plans/development-roadmap.md` | 4, 11, 12 |
