# Slackline

[![tests](https://github.com/facetdigital/slackline/actions/workflows/tests.yml/badge.svg)](https://github.com/facetdigital/slackline/actions/workflows/tests.yml)
[![release](https://img.shields.io/github/v/release/facetdigital/slackline)](https://github.com/facetdigital/slackline/releases/latest)
[![python](https://img.shields.io/badge/python-3.9%2B-blue)](pyproject.toml)
[![runtime dependencies](https://img.shields.io/badge/runtime%20dependencies-0-brightgreen)](pyproject.toml)
[![slack access](https://img.shields.io/badge/slack%20access-read--only-brightgreen)](#the-read-only-guarantee)
[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

Read-first Slack CLI tools for humans and AI agents. User-token only, no bot, no app
identity, no server.

```bash
slackline-read conversation --with @joan --with @kim --since 48h
```

That prints a clean transcript. Hand it to a model with "pull out the decisions and open
questions" and you have the thing this exists for. Slackline handles authentication,
identity resolution, retrieval, pagination, and formatting. The model handles
interpretation. Neither does the other's job.

**Status: v0.2.** Every read command is built, tested offline, and was verified end to end
against the live workspace on 2026-08-28 (the six PRD success criteria; see the Phase 1.0
entry in the [roadmap](docs/plans/development-roadmap.md)). See
[what is not built](#what-is-not-built-yet) before you plan around it.

---

## Quick start

About five minutes. You need Python 3.9 or newer, and a Slack workspace where you can
install an app (or ask an admin to approve one).

**1. Install.** From the latest release, no clone needed:

```bash
uv tool install https://github.com/facetdigital/slackline/releases/download/v0.2.1/slackline-0.2.1-py3-none-any.whl
# or: pipx install https://github.com/facetdigital/slackline/releases/download/v0.2.1/slackline-0.2.1-py3-none-any.whl
```

Or from a clone, if you want the source and the [skill](#the-slackline-skill):

```bash
git clone https://github.com/facetdigital/slackline.git
cd slackline && uv tool install .
```

**2. Create a Slack app and get a user token.** At
[api.slack.com/apps](https://api.slack.com/apps), choose **Create New App**, then **From a
manifest**, pick your workspace, and paste
[`docs/slack-app-manifest.yml`](docs/slack-app-manifest.yml). It asks for read scopes on a
user token and nothing else: no bot, no write scope. Choose **Install to Workspace**,
then copy the **User OAuth Token** (`xoxp-...`) from **OAuth & Permissions**.

**3. Hand it the token, and check who you are.** `read -rs` takes the token without
echoing it or writing it to your shell history:

```bash
read -rs SLACK_USER_TOKEN && export SLACK_USER_TOKEN
slackline-read whoami
```

**4. Read something.**

```bash
slackline-read channels --match general
slackline-read conversation --channel '#general' --since 24h
```

Next: [Credentials](#credentials) to keep the token somewhere better than a shell
variable, [Commands](#commands) for everything else, and
[Using with Claude Code](#using-with-claude-code) to hand it to an agent, including the
[skill](#the-slackline-skill) that teaches agents to use it.

---

## The read-only guarantee

`slackline-read` cannot post, edit, delete, react, join, or mark anything as read, under
any combination of flags. That is enforced by an allowlist of Slack methods in
[`api.py`](src/slackline/api.py), checked before every request, and guarded by a test that
fails if a write method is ever added to it.

The reason it is a separate executable rather than a flag is that the permission boundary
becomes an operating-system boundary. An agent that has only `slackline-read` on its PATH
cannot write to Slack. No prompt, config file, or model judgment is involved. Write
commands, when they exist, will be a different binary holding a different token.

---

## What this is not

Being read-only by construction is the product. So, to save everyone a round trip:

- **Not an MCP server.** It is a set of small executables. An agent runs them through a
  shell like any other command, and the permission boundary is which binaries are on
  its `PATH`, not a tool list inside a server.
- **Not a full Slack API wrapper.** It covers reading conversations well enough that a
  model can act on what it read: discovery, identity resolution, bounded transcripts,
  threads. Slack methods that serve none of that are out of scope, even read-only ones.
- **slackline-read never writes.** Not posting, not reacting, not joining, not marking
  as read. That is enforced by an allowlist of Slack methods checked before every
  request, with a test that fails if a write method is added, not by a promise or a
  flag. Anything that would weaken that is out of scope for `slackline-read`.
- **Writing is a separate, planned executable.** `slackline-write` will live in this
  project as its own binary with its own token, its own scopes, and its own allowlist,
  and every write it makes will need explicit confirmation from the human running it.
  It will never be a mode or flag of `slackline-read`. So "add a post command" is a
  `slackline-write` request, not a `slackline-read` one; see the
  [roadmap](docs/plans/development-roadmap.md), Phase 3.0.

---

## Install

Requires Python 3.9 or newer. Zero runtime dependencies. Each
[release](https://github.com/facetdigital/slackline/releases) carries a wheel, a source
tarball, and the skill as a zip.

```bash
# From a release, on your PATH, isolated
uv tool install https://github.com/facetdigital/slackline/releases/download/v0.2.1/slackline-0.2.1-py3-none-any.whl

# From a clone
uv tool install .

# Upgrading: from the new release URL, or from an updated clone. --reinstall makes uv
# rebuild the clone; without it, uv reuses its cached build whenever the version number
# has not changed, and you keep the old code.
uv tool install --force --reinstall .   # or: pipx reinstall slackline

# Or run it without installing, from the root of a clone
PYTHONPATH=src python3 -m slackline.cli_read --version
```

Confirm it landed:

```bash
slackline-read --version
```

---

## Credentials

Slackline uses a **user token** (`xoxp-...`), not a bot token. It reads exactly what you
can read, and nothing you cannot.

There is deliberately **no `--token` flag**. A token in `argv` is readable by any process
via `ps` and is written to your shell history.

Resolution order, first hit wins:

| # | Source | For |
|---|--------|-----|
| 1 | `SLACK_USER_TOKEN` environment variable | **Agents.** The only one an agent needs |
| 2 | macOS Keychain, service `slackline` | Humans on a Mac |
| 3 | 1Password, when `SLACKLINE_OP_REF` is set | Humans who want TouchID gating |
| 4 | `./.env` in the current directory | Local development |

`--token-file PATH` (CI, containers) sits outside that chain: when given, it is consulted
first, ahead of the environment, because an explicit instruction overrides the ambient
credential. A missing file is an error even when `SLACK_USER_TOKEN` is set.

### For an agent

```bash
SLACK_USER_TOKEN=$(op read --account your.1password.com \
  "op://Vault/Slack User API Token/credential") \
  slackline-read conversation --with @joan --since 24h
```

The runner resolves the secret once and passes it in. The agent never fetches a
credential, so it never triggers a TouchID prompt and never has a way to obtain a token on
its own.

Pass `--token-source env` in unattended jobs. It pins the source and disables the
fallback chain, so a missing credential fails loudly instead of blocking on an interactive
1Password prompt that nobody is there to answer.

### For a human

```bash
security add-generic-password -s slackline -a "$USER" -w   # prompts for the token
slackline-read whoami
```

---

## Commands

Every command takes `--format text|jsonl|json|raw`. `text` is the default and is meant to
be read. `jsonl` is meant to be piped. Diagnostics always go to stderr, so stdout carries
only the data you asked for.

### `whoami`

Which identity and workspace you are operating as, and what scopes the token holds.

```bash
slackline-read whoami
```

### `channels`

Every conversation you can read, without being told any channel IDs.

```bash
slackline-read channels
slackline-read channels --kind im,mpim
slackline-read channels --match eng
slackline-read channels --include-archived
```

Archived conversations are hidden unless asked for, and marked `(archived)` when shown;
in JSON output each row carries `"archived"`. Their history is still readable by
ID, and by name when no live conversation carries that name; a name that both a live and
an archived conversation carry resolves to the live one, and the archived namesakes are
announced on stderr. A conversations cache written before archived rows were fetched lacks them until
`--refresh-cache` or its one-hour TTL.

Group DM names are not unique. When two rows share a name they are two different
conversations, and reading one is not reading the other. Select those by ID.

### `users`

The workspace directory, searchable.

```bash
slackline-read users --match joan
```

### `resolve`

What *would* be read, without reading it. This is where an ambiguous selector gets
disambiguated; everywhere else ambiguity is a hard failure.

```bash
slackline-read resolve --with @joan --with @kim
```

Use it as a cheap dry run before a large pull, and to separate a selection bug from a
retrieval bug when output looks wrong.

### `conversation`

The main event. A bounded, time-windowed, thread-expanded transcript.

```bash
# The group DM with Joan and Kim, last two days
slackline-read conversation --with @joan --with @kim --since 48h

# A channel, with every thread inlined beneath its root
slackline-read conversation --channel '#engineering' --since 7d --threads full

# An explicit range, as JSONL, for a pipeline
slackline-read conversation --channel '#incidents' \
  --between 2026-08-20 2026-08-22 --format jsonl

# Several conversations in one call, each framed by its own header and page record
slackline-read conversation --channel '#eng' --channel '#ops' --since 24h
```

Selectors: `--channel`, `--with` (repeatable; group DMs are matched on **member set**,
never on name), `--conversation ID`. Name a person by `@handle` to pick exactly that
person. A bare name is a search: `--with Kevin` matches handles, real names, and display
names, and when two people match you get exit 4 with the candidates, never a guess. `--channel` and `--conversation` may be repeated to
read several conversations in one invocation; each gets its own header and trailing
`page` record.

Windows: `--since 48h|7d|2026-08-25`, `--between START END`, `--after START [--before END]`,
`--timezone IANA_NAME`.

Bounds: `--limit` per page, `--max-messages` overall (default 500), `--all-pages`,
`--cursor` to continue.

Filtering: join and leave noise is excluded by default (`--include-system` restores it),
bots are included by default (`--exclude-bots`), `--no-reactions` drops reaction summaries.
**Whatever is filtered is counted and reported**, so a subset never looks like a quiet
conversation.

### `thread`

One exchange, root and replies, when you already hold the `ts`.

```bash
slackline-read thread --channel '#engineering' --ts 1756310012.000100
```

The `ts` is the `ts` field of any message in a previous read's output, and the trailing
number in a Slack permalink: `archives/C123/p1756310012000100` is `1756310012.000100`.
A reply's `ts` works too; it costs one extra call to find the root, and says so on stderr.

Bounds: `--limit` per page, `--max-messages` (default 500), `--all-pages`, `--cursor` to
continue an incomplete read.

---

## Reading the output

Two rules make the output safe for a model to act on.

**Nothing is silently dropped.** Blocks, attachments, files, and unknown block types all
render. A message with no renderable content at all is reported as such rather than
skipped. If Slack ships a block type this code has never seen, you get a labelled
placeholder, not silence.

**A subset always says so.** Every read ends with a `page` record:

```
-- 47 messages; 3 filtered out; more available, pass --all-pages or use the cursor
```

A read that hits its ceiling **exits 0** and reports the truncation there. It succeeded;
it just did not finish. Exiting non-zero would make every long conversation look like an
error to `set -e` and to most agent harnesses. The cursor in that record is exact for
top-level history: each request asks Slack for the remaining budget, so the ceiling lands
on a page boundary and `--cursor` resumes at the first message you have not seen, with
nothing skipped and nothing repeated. A cursor belongs to one conversation and cannot be
combined with several selectors.

Under `--threads full`, a thread with more replies than one page is counted in the same
record (`1 thread not fully expanded, pass --all-pages`) rather than silently shortened,
and `--all-pages` removes that per-thread bound entirely. One qualification: a broadcast
reply whose root falls on the far side of a page boundary appears once from history and
again under its root on the next page. It is never dropped, which is the failure that
would matter.

---

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success, **including a truncated or paginated read** |
| 1 | Slack API or unexpected failure |
| 2 | Invalid arguments |
| 3 | Authentication failure or missing scope |
| 4 | Ambiguous selector: candidates are printed, pick one by ID |
| 5 | Not found, or not visible to this user |
| 7 | Refused by the read-only method allowlist |
| 130 | Interrupted (Ctrl-C), by shell convention |

6 is deliberately unassigned. It was proposed for "partial results", and leaving it
unclaimed keeps that meaning from creeping back in against D16.

---

## The agent recipe

```bash
# 1. Confirm who you are
slackline-read whoami --format json

# 2. Find the conversation, and fail loudly if it is ambiguous
slackline-read resolve --with @joan --with @kim --format json

# 3. Read it
slackline-read conversation --conversation G0123456789 --since 24h --threads full

# 4. Check the trailing page record before concluding you have everything
```

Step 2 matters. `--with @joan --with @kim` matches on the **member set** of the group
DM, not its name, because Slack's group DM names are neither unique nor kept current. If
two conversations match, you get exit 4 and the candidates rather than a guess.

---

## Using with Claude Code

Slackline's whole design is that an agent needs nothing but the executable and one
environment variable. On a fresh machine:

```bash
git clone git@github.com:facetdigital/slackline.git
cd slackline
uv tool install .          # or: pipx install .
slackline-read --version   # confirm it is on PATH
```

Launch Claude Code with the token in its environment. The runner resolves the secret and
passes it in; Claude's shell commands inherit it, nothing is written to disk, and TouchID
prompts once at launch:

```bash
SLACK_USER_TOKEN=$(op read --account your.1password.com   "op://Vault/Slack User API Token/credential") claude
```

Then allow the executable once, so reads do not prompt every time: run `/permissions`
inside Claude Code and add the rule `Bash(slackline-read:*)` to the project's
`.claude/settings.local.json`. That rule is the permission boundary working as designed:
the agent gets the read-only binary and nothing else.

First prompts to try:

- "Run `slackline-read whoami` and tell me which workspace you are in."
- "Read my DM with @joan for the last 48 hours and pull out decisions and open questions."
- "List the channels I can see that mention deploy."

For unattended sessions add `--token-source env` to invocations (or ask the agent to),
so a missing credential fails loudly instead of falling through toward anything
interactive. The `.env` file is for developing Slackline itself, not for using it.

### The slackline skill

The repo ships a Claude Code skill at [.claude/skills/slackline](.claude/skills/slackline/SKILL.md)
that makes agents reach for `slackline-read` on any Slack read, with precedence over
any Slack MCP or API the session has. Inside this repo it is active automatically.

Install it either for every Claude Code session, or for one repo only, where it activates
just for sessions in that repo.

**From a release** (no clone needed): download `slackline-skill-X.Y.Z.zip` from the
[latest release](https://github.com/facetdigital/slackline/releases/latest), then:

```bash
unzip slackline-skill-X.Y.Z.zip -d ~/.claude/skills/              # every session
unzip slackline-skill-X.Y.Z.zip -d /path/to/repo/.claude/skills/  # one repo only
```

**From a clone:**

```bash
cp -r .claude/skills/slackline ~/.claude/skills/
```

or copy the same directory into another repo's `.claude/skills/` for that repo only. (Or
symlink it from your clone if you prefer updates to arrive with `git pull`.) After
upgrading Slackline, replace the skill alongside reinstalling the binary.
The skill never bundles the binary; someone who clones such a repo onto a machine
without `slackline-read` gets told by the skill itself where to get it. The skill's
frontmatter carries `version:`, matching the Slackline release it shipped with, so a
copy can always be compared against [VERSION](VERSION) or the release tags to see
whether it is stale.

---

## Feedback

Bugs, rough edges, or confusing behavior in the binary or the skill are worth a GitHub
issue on this repo. The bar for "worth it" is low. Before asking for a new command, read
[what this is not](#what-this-is-not). Pull requests are welcome; start with
[CONTRIBUTING.md](CONTRIBUTING.md). Security problems go through
[SECURITY.md](SECURITY.md), not an issue.

---

## The audit log

Every invocation appends one line to `~/.local/state/slackline/audit.jsonl`: timestamp,
command, argv with any token-shaped string redacted, the conversation IDs read, the
window, the message count, the duration, and the exit code.

**Never message content.** A log that carried it would be a second, unmanaged copy of the
workspace living in a dotfile, which is a worse liability than the one the log exists to
address.

Move it with `SLACKLINE_AUDIT_LOG=/some/path`, switch it off with
`SLACKLINE_AUDIT_LOG=none`. Failing to write it is a warning, never a failure of the read.
The file is owner-only, and so is the directory Slackline creates for it; a directory you
point it at is left as it is. The path may not be a symlink (it is refused with a
warning), and it belongs in a directory only you can write.

---

## The directory cache

Resolving `@joan` needs the user directory, and building it means paginating the whole
workspace, so the directory is cached on disk: `users-<team>.json` (24h),
`conversations-<team>.json` (1h), `members-<team>.json` (24h, group DM membership,
which Slack never changes), and `external-users-<team>.json` (24h, see below) under
`$SLACKLINE_CACHE_DIR`, else `$XDG_CACHE_HOME/slackline`, else `~/.cache/slackline`. It holds names, IDs, handles, display names, email addresses,
archived flags, and group DM membership. **Never message content.** The directory is
owner-only. `--refresh-cache` rebuilds it.

People from another company in a shared channel (Slack Connect) are not in your
workspace's directory. Slackline names them from the profile Slack attaches to their
messages, else with one `users.info` lookup per person, and marks them:
`@Jane Doe (ext)`, as a speaker and in mentions. JSONL carries `"external": true` and
their `team`. Someone it cannot name at all renders as their ID; the read still succeeds.

---

## Development

Setting up a checkout, running the offline suite and the opt-in live checks, the rules
every change follows, and how releases are cut: [CONTRIBUTING.md](CONTRIBUTING.md).

---

## What is not built yet

Named here so nobody plans around a capability that does not exist.

| Not built | Where it went |
|-----------|---------------|
| **Any write capability**: post, reply, react | Roadmap Phase 3.0, `slackline-write`, a separate binary with a separate token, never a flag of `slackline-read`. Deliberately gated behind a phase gate on this one |
| **Search** (`search.messages`) | Phase 2.1, maybe. Read-only and allowlist-compatible, but it needs the `search:read` scope and nothing has needed it yet; built only if users ask |
| **Permalinks** (`--include-permalinks`) | Phase 2.0 |
| **`--policy` allowlist and denylist files** | Phase 2.0 |
| **Shell completions** | Phase 2.0 |

Full detail: [roadmap](docs/plans/development-roadmap.md),
[requirements](docs/requirements/product-requirements.md),
[architecture](docs/architecture/architecture.md).

---

## License

MIT. See [LICENSE](LICENSE).
