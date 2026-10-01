# Plan: The Slackline Skill, v0.1.1

## Execution Instructions

Standard rules apply: work step-by-step; test-first where a step changes behavior; run
the suite as `.venv/bin/python -m pytest -q` and check its exit status directly; commit
per step; docs move with the code; one branch (`feat/slackline-skill`), one PR, merged
`--no-ff`; no emdashes in anything written.

**Legend:** `Dn` cites [architecture.md](../architecture/architecture.md). `Pn` cites
[product-requirements.md](../requirements/product-requirements.md).

---

## Summary

Ship a Claude Code skill named `slackline`, in this repository, so any agent session
discovers `slackline-read` when a Slack read comes up, instead of reaching for a Slack
MCP or raw API calls. The skill's stance: for any read operation from Slack, use
`slackline-read` via this skill, never any other method or MCP; this skill takes
precedence. Writes are explicitly out of scope for now (use whatever the session
provides, deliberately) and the skill will claim writes too when `slackline-write`
ships. Installation is agnostic, the way an open-source skill kit would do it: the
skill lives in-repo (active automatically for sessions inside the repo) and installs
globally with one copy command, no assumptions about anyone's dotfiles. Version bumps
to 0.1.1 because the deliverable changes what a clone of this repo provides.

Decided against for now, recorded in the roadmap: making the binary live inside the
skill directory. Skills are instructions, not boundaries; the executable boundary (PRD
section 2, R3, D2) requires independently installable binaries, especially once
`slackline-write` exists. A future plugin (Phase 3.1) may *bundle* a copy of the
package and run it via `python3 -m` as a convenience, while the repo stays canonical.

## Requirements

- Skill name `slackline`, at `.claude/skills/slackline/SKILL.md`
- Reads: absolute precedence over any other Slack tool or MCP; writes: not this skill,
  for now, and say so
- Credential stance: expect `SLACK_USER_TOKEN`; if absent, stop and tell the user how
  to relaunch; never hunt for credentials (no `op`, no keychain, no `.env` reading)
- The agent recipe: whoami, resolve, read, check the trailing page record; exit-code
  branching including 8 and 130
- A drift test: every flag and command the skill names must exist in the CLIs' help
- Agnostic install documented in the README, plus a feedback pointer
- Version 0.1.1 in both `pyproject.toml` and `slackline/__init__.py`, pinned equal

---

### Step 1: The skill and its drift test

- [x] Write the failing test first: `tests/test_skill.py` (new)
  - `test_the_skill_exists_with_frontmatter`: `.claude/skills/slackline/SKILL.md`
    exists; frontmatter contains `name: slackline` and a non-empty `description:`.
  - `test_every_flag_the_skill_names_exists`: collect every `--[a-z-]+` token from the
    skill body; assert each appears in the union of `cli_read.USAGE` and every command
    module's `HELP`. A flag the skill invents, or one removed from the tool but not the
    skill, fails here.
  - `test_every_command_the_skill_names_exists`: every `slackline-read <word>`
    invocation in the skill names a member of `cli_read.COMMANDS`.
  - `test_the_skill_states_the_precedence_and_the_credential_stance`: the body contains
    the phrases `takes precedence`, `never`, and `SLACK_USER_TOKEN`, and does not
    contain `op read` outside the relaunch instruction addressed to the user (assert
    the string `never` appears within the credentials section; keep this assertion
    simple: `"do not hunt for credentials"` present).
  Fails: no skill file.
- [x] Write `.claude/skills/slackline/SKILL.md`:
  - Frontmatter: `name: slackline`; `description:` tuned to trigger on reading Slack:
    messages, conversations, channels, DMs, threads, "what did X say", channel or user
    lookup, Slack history or transcripts; states it takes precedence over any other
    Slack tool or MCP for reads.
  - Precedence section, in substance: "For any read operation from Slack, use
    `slackline-read` via this skill, never any other method or MCP. This skill takes
    precedence. Writing to Slack (posting, reacting, editing) is not covered by this
    skill yet: use whatever write mechanism the session provides, deliberately and
    with the user's confirmation. When `slackline-write` ships, this skill will cover
    writes the same way."
  - Credentials section: the tool reads `SLACK_USER_TOKEN` from the environment. If it
    is missing (`whoami` exits 3), stop and tell the user to relaunch Claude Code with
    the token in the environment (show the generic
    `SLACK_USER_TOKEN=$(op read "op://Vault/item/credential") claude` shape). Do not
    hunt for credentials: no `op read`, no keychain access, no reading `.env` files.
    Pass `--token-source env` in unattended runs.
  - If the binary is missing (`command -v slackline-read` fails): tell the user to
    install it (`uv tool install .` from a clone, or `pipx install .`); do not attempt
    to install it yourself.
  - The recipe: `whoami` once to confirm identity; `resolve` before large pulls and
    whenever a selector might be ambiguous (exit 4 prints candidates; pick by ID);
    `conversation --with @a --with @b --since 48h` and `thread --ts ...` for reads;
    `--threads full` when the discussion lives in threads; always check the trailing
    `page` record before claiming a transcript is complete, and continue with
    `--cursor` when it is not.
  - Exit codes, briefly: 0 success including truncated; 2 arguments; 3 credential
    (relaunch); 4 ambiguous (use `resolve`, pick by ID); 5 not found or not visible;
    7 the read-only allowlist; 130 interrupted. **As built:** exit 8 is not documented
    yet; it ships with Phase 2.0's policy PR, and the skill describes the tool as it is.
  - Formats: default `text` for reading and summarizing; `--format jsonl` when
    processing programmatically.
- [x] Verify green.

**Satisfies:** PRD section 3 (headless agents first), P16, P18, D3's spirit at the tool-selection layer; roadmap item "Slackline skill"

**File(s):** `.claude/skills/slackline/SKILL.md`, `tests/test_skill.py`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_skill.py -q
```

**Commit message:** `Add the slackline skill: reads go through slackline-read, nothing else`

---

### Step 2: Agnostic install docs and a feedback pointer

- [x] Test-first: n/a, documentation. Verification is the greps below.
- [x] `README.md`, in the "Using with Claude Code" section: the skill is active
  automatically for sessions inside this repo; install it globally with
  `cp -r .claude/skills/slackline ~/.claude/skills/` (mention the symlink variant for
  people who keep the clone and want updates on `git pull`, without assuming any
  particular layout); re-copy after upgrading, next to the existing
  `uv tool install .` upgrade note.
- [x] `README.md`: a short "Feedback" section: this is not (yet) open source, but if
  you were given access, open a GitHub issue on the repo for bugs or rough edges in
  the binary or the skill.
- [x] Verify: `grep -c "cp -r .claude/skills/slackline" README.md` is 1; suite green.

**Satisfies:** PRD section 3; the act-like-open-source intent recorded in the roadmap

**File(s):** `README.md`

**Test:**
```bash
grep -n "cp -r .claude/skills/slackline" README.md
.venv/bin/python -m pytest -q
```

**Commit message:** `Document the skill's agnostic install and where feedback goes`

---

### Step 3: Version 0.1.1

- [x] Write the pinning test first: `tests/test_package.py` gains
  `test_the_version_is_the_same_everywhere`: `slackline.__version__` equals the
  `version` in `pyproject.toml` (read the file, one regex; no tomllib dependency
  question on 3.9). This passes today (both 0.1.0) and is a pin so the two can never
  drift; say so in its docstring.
- [x] Bump both to `0.1.1`. Note in the README install section that upgrading is
  `uv tool install --force .` (or `pipx reinstall`) from the updated clone.
- [x] Verify green.

**Satisfies:** roadmap item "Slackline skill, v0.1.1"

**File(s):** `pyproject.toml`, `src/slackline/__init__.py`, `tests/test_package.py`, `README.md`

**Test:**
```bash
.venv/bin/python -m pytest tests/test_package.py -q
```

**Commit message:** `Bump to 0.1.1: a clone now ships the skill as well as the binary`

---

### Step 4: Close out

- [x] Test-first: n/a.
- [x] Roadmap: move this item to Completed with the date and PR number. Confirm the
  Phase 3.1 entry carries the plugin/bundling design question (added when this plan
  landed). Phase 2.0 returns to Next Immediate Step, still Ready to implement.
- [x] Full suite green. **As built:** the roadmap move landed on the branch per review,
  and the global install (`cp -r`) happens right after merge. The out-of-repo trigger
  check needs a fresh session, so it is recorded in the PR as a checkbox for the next
  human session rather than claimed here.

**Satisfies:** roadmap item close-out

**File(s):** `docs/plans/development-roadmap.md`, `docs/plans/slackline-skill.md`

**Test:**
```bash
.venv/bin/python -m pytest -q
```

**Commit message:** `Mark the slackline skill shipped`

---

## Files Modified (Summary)

| File | Steps |
|------|-------|
| `.claude/skills/slackline/SKILL.md` | 1 |
| `tests/test_skill.py` | 1 |
| `README.md` | 2, 3 |
| `pyproject.toml` | 3 |
| `src/slackline/__init__.py` | 3 |
| `tests/test_package.py` | 3 |
| `docs/plans/development-roadmap.md` | 4 |
