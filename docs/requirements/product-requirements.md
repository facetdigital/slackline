# Slackline: Product Requirements

**Status:** current
**Owner:** Scott Bradley
**Last updated:** 2026-08-27

## 1. What Slackline is

Slackline is a suite of small, single-purpose command-line tools that read from and
(later) write to Slack, designed so that an AI agent can use them as reliably as a
human can.

It is not an MCP server, an app, a bot, or a daemon. It is a set of executables that
take arguments, do one thing, print a result, and exit.

The first and defining tool is:

```bash
slackline-read conversation --with @joan --with @kim --since 48h
```

An agent runs that, gets a clean transcript back, and hands it to a model with a task
like "pull out the decisions and open questions." Slackline handles authentication,
identity resolution, retrieval, pagination, and formatting. The model handles
interpretation. Neither does the other's job.

## 2. Why a CLI rather than an MCP server

- **The permission boundary becomes an executable boundary.** An agent that only has
  `slackline-read` on its PATH cannot write to Slack. That is enforced by the operating
  system, not by a prompt, a config file, or the model's good intentions.
- **It composes with everything.** Shell allowlists, containers, `sudoers`, CI secrets,
  Claude Code `Bash()` rules, and `ps` audit logs all understand executables already.
- **No connection to keep alive.** No server process, no socket, no lifecycle, no
  reconnection logic, no server that can be running a different version than you think.
- **The output contract is ours.** We shape the output for a language model rather than
  passing through Slack's response schema, which is verbose, deeply nested, and mostly
  metadata the model will never use.
- **It degrades to `curl`.** Any behavior can be reproduced by hand for debugging.

MCP is not foreclosed. If a standardized tool-discovery surface is ever wanted, an MCP
server can wrap these executables. The CLI stays the canonical implementation.

## 3. Users, in priority order

1. **Headless AI agents.** The primary consumer. Non-interactive, no TTY guarantee, no
   ability to answer a prompt, no ability to approve a TouchID dialog. Everything about
   the design assumes this caller first.
2. **Humans at a terminal.** A real audience. The transcript format exists for them, and
   they benefit from conveniences (Keychain, 1Password) that agents must never depend on.
3. **Scripts and pipelines.** `jq`, `rg`, cron. Served by the JSONL format and by honest
   exit codes.

## 4. The capability boundary

This is the central product decision and everything else bends around it.

| Executable | Authority | Status |
|---|---|---|
| `slackline-read` | Read only. Cannot mutate anything in Slack under any flag combination. | v0.1 |
| `slackline-write` | Creates messages and reactions. | planned |
| `slackline-admin` | Membership, channels, retention. Higher trust. | reserved, may never ship |

Rules that make the boundary real:

- **R1.** `slackline-read` calls only Slack methods that cannot mutate state. The
  permitted-method list is enforced in code, in one place, and a test asserts that no
  method outside it is reachable.
- **R2.** `slackline-read` never requests, and its token need never hold, any `:write`
  scope.
- **R3.** No umbrella binary in v0.1. A `slackline` dispatcher that could reach write
  commands would re-open the hole that splitting the binaries closed.
- **R4.** Read and write should use separate tokens with separate scopes. Splitting
  binaries limits accidental use. Splitting scopes limits blast radius. Both are wanted.

R1 deserves emphasis because it is subtler than it looks. `conversations.open` would be
a convenient way to resolve a group DM by member list, but it can *create* a DM as a side
effect. It is therefore banned from `slackline-read`, and the resolver takes a harder path
to stay honest. See the architecture document, decision D4.

## 5. Product requirements

### 5.1 Retrieval

- **P1.** Read messages from any conversation the authenticated user can see: public
  channels, private channels, direct messages, and group direct messages.
- **P2.** Select a conversation by channel name, by channel ID, by conversation ID, or by
  the set of people in it.
- **P3.** Bound the read by time: a relative window (`--since 48h`), an absolute range
  (`--between A B`), or open-ended endpoints (`--after`, `--before`).
- **P4.** Optionally expand threads inline, because a channel transcript without its
  thread replies is frequently missing the actual conversation.
- **P5.** Paginate correctly and make continuation visible to the caller rather than
  hiding it or silently truncating.

### 5.2 Output

- **P6.** Produce a compact human-readable transcript by default.
- **P7.** Produce JSON Lines on request, one self-contained record per line, streamable.
- **P8.** Render message content as something a language model can actually read. Slack
  message text is mrkdwn containing raw IDs, and a large fraction of messages carry no
  `text` at all. Turning that into readable prose is the single highest-value function
  Slackline performs. See section 7.
- **P9.** Never emit Slack's raw response as the default contract. Offer `--format raw`
  as an explicit debugging escape hatch.

### 5.3 Determinism and safety

- **P10.** Ambiguity is a hard failure, never a guess. If a selector matches more than one
  conversation or user, exit non-zero and print the candidates.
- **P11.** Machine output goes to stdout. Diagnostics, warnings, and progress go to stderr.
  Nothing else is ever written to stdout.
- **P12.** Exit codes are meaningful and stable enough for an agent to branch on.
- **P13.** Bound every read by default. An unbounded query must be requested explicitly,
  never arrived at by accident.
- **P14.** Tokens never appear in argv, in logs, in error messages, or in tracebacks.
- **P15.** Identical arguments produce identical output. Behavior never varies based on
  whether a TTY is attached.

### 5.4 Credentials

- **P16.** A headless agent must be able to run every tool with nothing but an environment
  variable. No TouchID, no interactive prompt, no keychain unlock, no daemon.
- **P17.** Humans may use nicer sources (Keychain, 1Password) as strictly optional
  conveniences layered on top.
- **P18.** There is no `--token` flag. Values in argv are readable by any process on the
  machine and are recorded in shell history.

## 6. Non-goals

- **Not a Slack client.** No unread state, no presence, no typing indicators, no UI.
- **Not a sync engine or a datastore.** Slackline reads on demand and forgets.
  Warehousing Slack history is a different tool's job, and Slackline deliberately does
  not do it.
- **Not a bot.** No app identity, no event subscriptions, no Socket Mode, no webhooks.
  Slackline acts as the authenticated human and nothing else.
- **Not multi-workspace in v0.1.** One token, one workspace per invocation.
- **Not a natural-language interface.** Selectors are explicit and deterministic. The
  model does the interpreting; the tool does not try to be clever.

## 7. Why message rendering is the core of the product

Reconnaissance against a real workspace on 2026-08-27 sampled roughly 376 messages
across 8 conversations. The results:

| Property | Share of sampled messages |
|---|---|
| Carries a `blocks` payload | 72% |
| **Has no `text` content at all** | **18%** |
| Contains a raw `<@Uxxxx>` user mention | 16% |
| Carries attachments | 19% |
| Carries reactions | 25% |
| Carries files | 10% |
| Contains `<http://...>` link markup | 8% |
| Contains `<!here>`, `<!channel>`, or a group mention | 7% |

Two conclusions follow, and they set the priorities for the whole project.

**A tool that reads `message["text"]` and stops is broken.** It silently drops
nearly one message in five, and it drops them without any error. The transcript looks
complete and is not. For an agent asked to summarize a decision, a silently missing
message is the worst possible failure: invisible, confident, and wrong.

**Raw Slack markup is close to unreadable, and expensive.** `<@U0ABC1234>` costs
several tokens and conveys nothing to the model. `@sam` costs one and conveys the
person. Across a long transcript this is both a comprehension problem and a cost problem.

Therefore: **P8 is the requirement that justifies Slackline existing at all.** Anyone can
paginate an HTTP endpoint. Turning Slack's wire format into something a model reads
correctly is the work.

## 8. Verified facts about the target environment

Established empirically on 2026-08-27 against a real workspace, not assumed.

- **Rate limiting is not currently a constraint for this token.** Four consecutive
  `conversations.history` calls at `limit=100` completed in about 0.22s each with no
  throttling, no `Retry-After`, and no rate-limit headers. The 2025 tightening of
  `conversations.history` for non-Marketplace apps does not appear to apply here.
  Slackline still implements full backoff and `Retry-After` handling, because this is a
  property of one app on one day, not a guarantee. But we are not designing around a
  restriction that does not exist.
- **The workspace is DM-shaped, not channel-shaped.** 398 accessible conversations:
  215 group DMs, 104 private channels, 76 direct messages, 3 public channels. Group DMs
  are the single largest category by a wide margin. `--with` resolution is therefore the
  main path, not an edge case.
- **Group DM names are not unique.** Two distinct conversation IDs in the workspace share
  the name `mpdm-sam--lee--bea-1`. Resolving a group DM by name alone is
  unsound. See architecture decision D4.
- **Granted scopes** on the working token: `identify`, `channels:history`,
  `groups:history`, `im:history`, `mpim:history`, `channels:read`, `groups:read`,
  `im:read`, `mpim:read`, `users:read`, `users:read.email`. No `search:read`, which is
  why search is a later phase. No write scope of any kind, which means the read tools are
  currently incapable of mutation even if they tried.

## 9. Success criteria for v0.1

The MVP is done when an agent, given only `slackline-read` and `SLACK_USER_TOKEN`, can:

1. Confirm which identity and workspace it is operating as.
2. Discover the conversations available to it without being told channel IDs.
3. Resolve "the group DM with Joan and Kim" to exactly one conversation, or fail
   loudly with candidates.
4. Retrieve a bounded, time-windowed, thread-expanded transcript in which mentions are
   named, links are readable, and no message is silently dropped.
5. Know from the output whether it received everything, and continue if not.
6. Do all of the above with no TTY, no prompt, and no possibility of writing to Slack.
