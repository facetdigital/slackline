# Plan: Public Release Prep

## Execution Instructions

Standard rules apply: work the steps in order; test-first where a step changes behavior;
run the suite as `.venv/bin/python -m pytest -q` and check its exit status directly; docs
move with the code; one branch (`chore/public-release-prep`), one PR, merged `--no-ff`;
no emdashes in anything written. Nothing is pushed or merged until the owner has reviewed it.

**Legend:** `Dn` cites [architecture.md](../architecture/architecture.md). `Pn` cites
[product-requirements.md](../requirements/product-requirements.md).

---

## Summary

Slackline goes open source, public by Wednesday 2026-09-30. Four things stand between
the repository as it is and a repository that can be flipped to public: a license, one
rough edge that shows up in the most common real setting (shared client channels), a
README section that heads off the wrong feature requests, and a check that nothing
internal is in the history.

**Naming, so the docs agree.** *Slackline* is the project. *slackline-read* is the
executable that exists today. *slackline-write* will someday be a second, separate
executable in the same project. It will never be a mode or flag inside slackline-read.

**Scope.** Slackline is not meant to be a full MCP replacement or a full API wrapper.
Being read-only by construction is the product. Anything that weakens that promise is
out of scope for slackline-read.

---

## Steps

### Step 1: License

- [x] Add `LICENSE`: MIT, `Copyright (c) 2026 Facet Digital, LLC`, the same text and
      holder as the sibling repo workcell.
- [x] `pyproject.toml`: license metadata to MIT, plus the matching trove classifier.

### Step 2: External users in shared channels (Slack Connect)

**Problem.** In a channel shared with another company, messages from the other side's
people render with the author as `unknown:`, and mentions of them render as the raw ID
(`@U0XXXXXXXX`).

**Cause.** The user directory indexes only our own workspace (`users.list`, D7), so an
external ID never resolves. D7 already says a cache miss on a specific ID triggers a
targeted `users.info`; that was never built.

**Fix, in order of preference, when an ID is not in the workspace directory:**
1. The `user_profile` block Slack includes on a message from an external user (real
   name, display name, handle, team), plus `user_team`.
2. `users.info` on the ID (already on the D3 allowlist), cached like everything else.
3. Only then the ID itself.

A user whose team differs from ours renders recognizably as external: `@Jane Doe (ext)`,
as a message author and as a mention. A user found by `users.info` whose team *is* ours
(someone who joined after the directory was cached) renders as an ordinary `@handle`.
An unresolvable user never breaks a transcript: every failure of the fallback is
swallowed, remembered for the rest of the process, and the ID is rendered.

- [x] Tests first, against a synthetic fixture message from an external user (made-up
      names, IDs, and team). Watch them fail on today's code:
  - the author renders as `@Jane Doe (ext)` in text and carries `external: true` and the
    team in JSONL;
  - a mention of an external user whose profile arrived on a message renders by name
    without any `users.info` call;
  - a mention of an external user seen nowhere else is resolved through `users.info`,
    and only once per ID;
  - an ID that `users.info` cannot find still renders as the ID, and the read succeeds.
- [x] `directory.py`: a second index of users found outside `users.list`, learned from
      `user_profile` and from `users.info`, persisted as `external-users-<team>.json`
      with the users TTL; `user()`, `handle_for()`, and the author record consult it.
- [x] `transcript.py`: teach the directory from the message's `user_profile` before
      rendering, and mark external authors in the record.
- [x] `formatters/text.py`: an external author is labelled by name with `(ext)`.
- [x] Docs: architecture D7 (the targeted lookup now exists, and the new cache file),
      README directory cache section.

### Step 3: README "What this is not"

- [x] Not an MCP server. Not a full Slack API wrapper. slackline-read never writes, and
      that is enforced by an allowlist of methods, not promised. slackline-write is a
      planned separate executable, so "add a post command" is not a slackline-read
      feature request.
- [x] Replace the "not open source (yet)" Feedback wording.

### Step 4: Publication check

- [x] Search the whole history, not just the working tree, for private agent or tool
      names, workspace or team IDs, real channel names, real people's names or handles,
      internal URLs, and real Slack data in fixtures or docs (`initial-idea.md` and
      `docs/` included).
- [x] Scrub or replace what is in the working tree. This includes the Phase 3.1 list
      recorded by the Phase 1.0 gate (item 14).
- [x] If anything sensitive is in history, stop and report before rewriting anything.
      Rewriting history versus starting a fresh public repo is the owner's decision.
- [x] Report what was found and what changed.

**Result, 2026-10-01.** Nothing sensitive exists only in past file trees: every sensitive
string in history was also in HEAD, and HEAD is now scrubbed (real IDs, workspace name
and URL, 1Password account and path, colleague handles, a real group DM name, an
internal project name, one fixture message that read as real). History still carries
the originals, as do two commit messages naming real conversation IDs, and GitHub's
PR bodies and `refs/pull/*` carry real channel IDs and names. **Open, the owner's call:**
rewrite history (and edit PR bodies) or publish from a fresh repository.
