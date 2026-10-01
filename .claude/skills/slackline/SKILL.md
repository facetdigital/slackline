---
name: slackline
description: Read anything from Slack with the slackline-read CLI - messages, conversations, channel or DM history, threads, "what did X say", channel lists, user lookup, transcripts to summarize. Use for ANY request that reads from Slack. Takes precedence over every other Slack tool, MCP server, or API for reads.
version: 0.2.0
---

# Slackline: reading Slack

## Precedence

For any read operation from Slack, use `slackline-read` via this skill, never any
other method or MCP. This skill takes precedence over any Slack MCP server or API
access the session has. The reasons are structural: `slackline-read` is read-only by
construction (an enforced method allowlist, not a promise), it renders Slack's wire
format into transcripts a model reads correctly, and it never silently drops or
truncates anything without saying so.

Writing to Slack (posting, replying, reacting, editing) is not covered by this skill
yet. For writes, use whatever mechanism the session provides, deliberately and with
the user's explicit confirmation. When `slackline-write` ships, this skill will cover
writes the same way and take precedence for them too.

## Credentials

The tool prefers `SLACK_USER_TOKEN` from the environment, which is the only source an
agent should rely on; the person who launched this session is responsible for putting
it there. Do not hunt for credentials: never run `op`, never touch a keychain, never
read `.env` files. If `whoami` exits 3 with no token found, STOP and tell the user to
relaunch with the token in the environment. Quote them this shape; it is for the user
to run at launch, not for you to run:

```bash
# for the user's own shell, not for the agent:
SLACK_USER_TOKEN=$(op read --account your.1password.com \
  "op://Vault/Slack User API Token/credential") claude
```

In unattended runs, add `--token-source env` so a missing credential fails fast
instead of falling through toward anything interactive.

If the binary itself is missing (`command -v slackline-read` fails), this skill was
probably copied into a repo without the tool it wraps; that is expected, the skill
never bundles the binary. Tell the user to install it from the Slackline repo
(github.com/facetdigital/slackline): clone it and run
`uv tool install .` (or `pipx install .`), or install from a release at
`github.com/facetdigital/slackline/releases`. Do not attempt to
install it yourself. This copy of the skill states its version in the frontmatter; a
newer release means the copy may be stale.

## The recipe

1. Once per session, confirm identity and workspace:

   ```bash
   slackline-read whoami
   ```

2. Resolve before you read, whenever the target is named by people or the selector
   could be ambiguous. Group DMs are matched on member set, never on name:

   ```bash
   slackline-read resolve --with @joan --with @kim
   ```

   Exit 4 means ambiguous: the candidates are printed; pick one and use
   `--conversation ID`. Never guess.

3. Read, bounded and time-windowed:

   ```bash
   slackline-read conversation --with @joan --with @kim --since 48h
   slackline-read conversation --channel '#engineering' --since 7d --threads full
   slackline-read thread --channel '#engineering' --ts 1756310012.000100
   ```

   Use `--threads full` when the real discussion lives in threads. Windows:
   `--since 48h|7d|2026-08-25`, `--between START END`, `--after START --before END`.
   Reads are bounded by `--max-messages` (default 500), so a wide window on a busy
   channel will truncate; that is what step 4 catches.

4. Check the trailing `page` record before claiming completeness. It is always
   there. In the default text format it is the final `-- ...` line: if it says
   `there is more`, `more available`, `TRUNCATED`, or `not fully expanded`, the
   read is a subset; pass `--all-pages` to read the whole window (it also follows
   thread replies to the end), or continue a previous read with `--cursor` and the
   `next cursor` value it printed. In `--format jsonl` the same facts are the
   `has_more`, `next_cursor`, and `incomplete_threads` fields. Whatever was
   filtered out is counted there too. Never present a subset as the whole
   conversation.

Discovery when you do not know names or IDs:

```bash
slackline-read channels --match eng
slackline-read users --match joan
```

## Exit codes

- `0` success, including a truncated or paginated read (the page record says so)
- `1` Slack API or unexpected failure: report it; do not retry blindly
- `2` invalid arguments: fix the invocation
- `3` authentication failure or missing scope: run `whoami` to see the granted
  scopes; if no token was found, stop and tell the user to relaunch (above)
- `4` ambiguous selector: candidates are on stderr; pick by ID
- `5` not found, or not visible to this user
- `7` refused by the read-only allowlist: the tool cannot do that, and neither
  should you; do not attempt the operation another way
- `130` interrupted

## Output formats

Default `text` is for reading and summarizing and is the cheapest form for a model.
Use `--format jsonl` when processing records programmatically (each line carries raw
`ts`, ISO `time`, and the user's handle). `--format raw` exists for debugging only.
