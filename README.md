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

**Status: v0.2.2.** Every read command is built, tested offline, and was verified end to end against a real workspace (the six PRD success criteria; see the Phase 1.0 entry in the [roadmap](docs/plans/development-roadmap.md)). See [what is not built](#what-is-not-built-yet) before you plan around it.

---

## Quick start

About five minutes. You need Python 3.9 or newer, and a Slack workspace where you can install an app. If your workspace restricts app installs, you may need an admin to approve it.

**1. Install.** From the latest release, no clone needed:

```bash
uv tool install https://github.com/facetdigital/slackline/releases/download/v0.2.2/slackline-0.2.2-py3-none-any.whl
# or: pipx install https://github.com/facetdigital/slackline/releases/download/v0.2.2/slackline-0.2.2-py3-none-any.whl
```

Or from a clone, if you want the source and the [skill](#the-slackline-skill) (pick this one if you plan to use it with Claude Code):

```bash
git clone https://github.com/facetdigital/slackline.git
cd slackline && uv tool install .
```

**2. Create the Slack app and copy your user token.** Sign in to your Slack workspace in the browser first, then go to [api.slack.com/apps](https://api.slack.com/apps) and choose **Create New App**, then **From a manifest**.

1. Pick your workspace from the dropdown, choose the **YAML** tab, and replace its contents with [`docs/slack-app-manifest.yml`](docs/slack-app-manifest.yml). It asks for read scopes on a user token and nothing else: no bot, no write scope. Click **Next**.
2. Click **Create and Install**. In the window that opens, check the workspace is the right one and click **Allow**.
3. A window titled **Slackline is ready!** opens. Expand **Your app credentials** at the top. It lists a **User token** and an **App Token**; copy the **User token** (it starts `xoxp-`). Leave the window open until step 3 is done.

If your workspace only lets admins install apps, Slack tells you "You don't have permission to install apps" and offers to let you create the app and request the install. Do that; once an admin approves it, come back to the app at [api.slack.com/apps](https://api.slack.com/apps) to install it and copy the user token.

**3. Hand the token to your shell.** Run this, paste the token, and press Return:

```bash
read -rs SLACK_USER_TOKEN && export SLACK_USER_TOKEN
```

It shows no prompt and echoes nothing, which keeps the token off your screen and out of your shell history. Now close the Slack window with the **X** in its top right corner.

**4. Check who you are,** in the same terminal:

```bash
slackline-read whoami
```

You should see your user, your workspace, and the scopes the token holds:

```
User:      you (U0123ABCDE)
Workspace: Your Workspace (T0123ABCDE)
URL:       https://your-workspace.slack.com/

Granted scopes:
  channels:history
  channels:read
  groups:history
  groups:read
  identify
  im:history
  im:read
  mpim:history
  mpim:read
  users:read
  users:read.email

This token holds no write scopes. slackline-read is read-only.
```

The manifest does not ask for `identify`; Slack grants it to every user token.

**5. Read something.** Use a channel that exists in your workspace; swap it in for `general` if yours has none by that name.

```bash
slackline-read channels --match general
slackline-read conversation --channel '#general' --since 24h
```

Next: [Using with Claude Code](#using-with-claude-code) to hand it to an agent, including the [skill](#the-slackline-skill) that teaches agents to use it; [Commands](#commands) for everything else; and [Credentials](docs/credentials.md) to keep the token somewhere better than a shell variable.

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
uv tool install https://github.com/facetdigital/slackline/releases/download/v0.2.2/slackline-0.2.2-py3-none-any.whl

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

### Uninstall

Remove the binary, and the skill if you copied it anywhere:

```bash
uv tool uninstall slackline          # or: pipx uninstall slackline
rm -rf ~/.claude/skills/slackline    # only if you installed the skill there
```

Remove what Slackline keeps on disk, the [directory cache and audit log](docs/reference.md), and the Keychain item if you made one:

```bash
rm -rf ~/.cache/slackline ~/.local/state/slackline
security delete-generic-password -s slackline   # macOS, only if you stored the token there
```

Then delete the Slack app, which revokes its token: at [api.slack.com/apps](https://api.slack.com/apps), open **Slackline**, and on **Basic Information** choose **Delete App** at the bottom of the page.

---

## Credentials

Slackline uses a **user token** (`xoxp-...`), not a bot token, so it reads exactly what you can read. It looks for the token in the `SLACK_USER_TOKEN` environment variable first, then the macOS Keychain, then 1Password if you configure it, then `./.env`. There is deliberately no `--token` flag, because `argv` is visible to every process via `ps` and lands in shell history.

An agent needs only `SLACK_USER_TOKEN` in its environment, set any way you like. The `read -rs` from the quick start is enough; 1Password is one option, not a requirement. The full chain, `--token-file`, unattended runs, and the Keychain setup are in [docs/credentials.md](docs/credentials.md).

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

Two rules make the output safe for a model to act on. **Nothing is silently dropped**: blocks, attachments, files, and unknown block types all render, and a message that cannot be rendered is reported rather than skipped. **A subset always says so**: every read ends with a `page` record such as

```
-- 47 messages; 3 filtered out; more available, pass --all-pages or use the cursor
```

and a read that hits its ceiling still **exits 0**, because it succeeded; it just did not finish. Exit 4 means an ambiguous selector, with the candidates printed. Cursors, thread bounds, the full exit-code table, and what Slackline keeps on disk (an audit log and a directory cache, never message content) are in [docs/reference.md](docs/reference.md).

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

Slackline's whole design is that an agent needs nothing but the executable and one environment variable, `SLACK_USER_TOKEN`. If you did the quick start from a clone, you have both. Start Claude Code inside the clone, from the same terminal where you exported the token, so it inherits the variable and picks up the [skill](#the-slackline-skill):

```bash
cd slackline
claude
```

Nothing is written to disk; the token lives in that shell's environment and goes away with it. To start Claude from a fresh terminal later, put the token back in the environment first, by repeating the quick start's `read -rs`, or by resolving it at launch from wherever you keep it. With 1Password, for example, replacing the account and the `op://` reference with your own:

```bash
SLACK_USER_TOKEN=$(op read --account your.1password.com \
  "op://Vault/Slack User API Token/credential") claude
```

Depending on your permission settings, Claude Code may ask before it runs `slackline-read`. To allow it once rather than on every read, run `/permissions` inside Claude Code and add the rule `Bash(slackline-read:*)` to the project's `.claude/settings.local.json`. That rule is the permission boundary working as designed: the agent gets the read-only binary and nothing else.

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

## Development

Setting up a checkout, running the offline suite and the opt-in live checks, the rules
every change follows, and how releases are cut: [CONTRIBUTING.md](CONTRIBUTING.md).

---

## What is not built yet

Named here so nobody plans around a capability that does not exist.

| Not built | Where it went |
|-----------|---------------|
| **Any write capability**: post, reply, react | Roadmap Phase 3.0, `slackline-write`, a separate binary with a separate token, never a flag of `slackline-read`. Deliberately gated behind a phase gate on this one |
| **Downloading attachments** | Next on the [roadmap](docs/plans/development-roadmap.md). Today a file shows in the transcript as `[file: name (type, size)]`, but its bytes cannot be fetched: that needs the `files:read` scope |
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
