# Slackline: Architecture

**Status:** current
**Last updated:** 2026-08-30

Companion to [product-requirements.md](../requirements/product-requirements.md). This
document records numbered decisions (D1, D2, ...) that implementation plan steps cite in
their **Satisfies:** lines.

---

## 1. Shape

One Python package, zero runtime dependencies, one installed executable in v0.1.

```
slackline/
  pyproject.toml
  src/slackline/
    __init__.py
    cli_read.py         entry point for slackline-read: argv parsing, dispatch, exit codes
    commands/
      _common.py        selector and time-window flag parsing shared by resolve,
                        conversation, and thread
      whoami.py         auth.test
      channels.py       conversation inventory
      users.py          user directory
      resolve.py        selector to conversation ID, inspectable
      conversation.py   the main event
      thread.py         a root message and its replies
    api.py              HTTP, retry, pagination, method allowlist
    auth.py             credential resolution chain
    cache.py            on-disk user and conversation index
    resolve.py          selector engine
    render.py           Slack wire format to readable text  <- the core value
    transcript.py       the message record shape and the bounded fetch loop both
                        transcript commands share
    timeparse.py        time window parsing
    audit.py            local audit log
    errors.py           exception hierarchy mapped to exit codes
    formatters/
      __init__.py       jsonl, json, and raw formatters, and the registry
      text.py           human transcript
  tests/
    fixtures/           recorded and synthesized Slack payloads
```

### D1. Zero runtime dependencies, Python 3.9+

`urllib.request` from the standard library rather than `httpx` or `requests`, and no Slack
SDK.

The Slack Web API is HTTPS with JSON bodies and query-string parameters. Nothing about it
needs a client library. In exchange for writing about eighty lines of HTTP handling, the
tool installs anywhere Python exists, runs from a copied directory with no virtualenv, has
no dependency tree to audit or resolve, and cannot break because a transitive dependency
did. For a tool whose entire premise is that an agent can run it with no setup friction,
that trade is obviously correct.

3.9 is the floor because it is the stock macOS interpreter. `from __future__ import
annotations` at the top of every module keeps modern type syntax available.

The Slack SDK is rejected on the same grounds plus one more: it would make it harder, not
easier, to enforce D3.

### D2. One executable in v0.1

`slackline-read` only. No `slackline` umbrella. Per PRD R3, a dispatcher that can reach
write commands would defeat the reason for splitting the binaries.

```toml
[project.scripts]
slackline-read = "slackline.cli_read:main"
```

`PYTHONPATH=src python3 -m slackline.cli_read`, from the root of a clone, works identically for the no-install case.

---

## 2. The read-only guarantee

### D3. A method allowlist, enforced in one place

`api.py` holds a frozen set of permitted Slack methods. Every request passes through a
single function that rejects anything not in the set before a socket is opened.

```python
READ_ONLY_METHODS = frozenset({
    "auth.test",
    "conversations.history",
    "conversations.replies",
    "conversations.info",
    "conversations.members",
    "users.conversations",
    "users.info",
    "users.list",
    "team.info",
})
```

Three properties make this worth doing rather than trusting ourselves:

- It is one short list, readable in full in a code review.
- A test asserts that every Slack method name appearing anywhere in the package is a
  member of the set, so a future command cannot quietly widen the authority.
- It fails closed. An unknown method is refused, not attempted.

This is defense in depth. The token in use holds no write scope, so Slack would refuse a
write anyway. The allowlist means we do not depend on that being true forever.

Deliberately excluded despite being convenient:

| Method | Why it is banned |
|---|---|
| `conversations.open` | Can create a group DM as a side effect. This is the one that hurts, see D4. |
| `conversations.mark` | Mutates read state. Reading should not change what the human sees as unread. |
| `search.messages` | Not a mutation, but needs `search:read` which the token lacks. Phase 2.1. |
| `conversations.list` | Not banned in principle, but `users.conversations` is strictly better here, see D5. |

`conversations.members` was added to the allowlist during implementation. It is read-only,
and it turned out to be the only method that actually returns a group DM's membership.

---

## 3. Credentials

### D4a. Resolution chain, headless first

Ordered, first hit wins, each step skipped silently if unavailable:

1. `SLACK_USER_TOKEN` environment variable
2. macOS Keychain, service `slackline`
3. 1Password via `op read`, if `SLACKLINE_OP_REF` is set
4. `./.env` in the current directory

`--token-file PATH` is not a step in that chain. It is an explicit instruction, so when it
is given it is consulted first, ahead of the environment. Leaving it lower would silently
do the opposite of what the caller asked whenever `SLACK_USER_TOKEN` happened to be set,
and would make "the file does not exist" unreachable. A missing file is therefore an error
even when the environment holds a token. (Revised 2026-08-30 to match the code, which has
worked this way since the core plumbing review.)

There is no `--token` flag (PRD P18).

The ordering is the whole point. Step 1 is the only one a headless agent needs, and it is
first, so an agent never falls through into something that blocks on a human. Steps 2 and
3 are conveniences for a person at a terminal. Step 4 is for development in this repo.

Slackline's global convention is that secrets resolve through 1Password with TouchID
gating. That convention exists to stop a background process from silently grabbing a
secret, and it is not violated here: the headless carve-out is that **the runner passes
the secret in**, rather than the tool reaching out to fetch one. A Slackline process
started without `SLACK_USER_TOKEN` in its environment has no file to read and must fall
through to `op`, which prompts. The tripwire still fires.

`--token-source {env,file,keychain,op,dotenv}` forces one source and disables the
fallthrough, so a headless job can fail loudly instead of hanging on a TouchID prompt that
nobody will answer.

### D4b. Token hygiene

The token is read into one variable, used only as an `Authorization` header, and never
logged. `errors.py` scrubs any string matching `(?:xox[a-z]|xapp)-[A-Za-z0-9%/+=._-]+`
(case-insensitive) from every message before it can reach stderr, including inside
tracebacks. The pattern is deliberately wide: any `xox?-` or `xapp-` prefix and the whole
URL-safe run after it, because a narrower character class would stop at the first `%`,
`/`, `+` or `=` in a session token and leave the tail of the secret in the output looking
redacted. A test asserts a token placed in an exception message does not survive
formatting.

---

## 4. Resolution

### D5. `users.conversations`, not `conversations.list`

`conversations.list` enumerates the workspace. `users.conversations` enumerates what *this
user* is in, which is exactly the authority boundary Slackline operates within. It is
smaller, faster, and structurally incapable of returning something the user cannot read.

Measured: 398 conversations across 3 pages at `limit=200`.

### D6. Group DM resolution must verify member sets, because names collide

This is the hardest correctness problem in the MVP and the reconnaissance changed the
design.

Slack names group DMs by convention:

```
mpdm-sam--lee--bea-1
      ^^^^^^^^^^^^^^^^^^^^^^^^^^^^ usernames joined by "--", then "-<n>"
```

215 of 215 group DMs in the workspace match that pattern, so parsing it is reliable, and
the double-hyphen separator means usernames containing single hyphens survive the split
intact.

The trap: **two distinct conversation IDs in the workspace share the name
`mpdm-sam--lee--bea-1`.** Slack re-creates a group DM under the same name
when the same set of people start a new one. Resolving by name alone would silently pick
whichever came back first, and the agent would read the wrong conversation with no
indication anything was wrong.

The obvious fix, `conversations.open --users U1,U2 --prevent_creation`, is banned by D3
because that method can create a conversation.

**Revised 2026-08-27 after implementation.** Two further facts, both found only by running
against the live workspace, changed this design after it was first written. They are
recorded here rather than quietly fixed, because each invalidated something the original
plan asserted.

**`conversations.info` does not return a `members` key for group DMs.** The original
design called for verifying membership with it. Against real data it returns no member
list at all, so that step would have silently fallen through to the cached name-derived
membership and verified nothing. `conversations.members` is the method that answers the
question, and it is read-only, so it joins the allowlist.

**Group DM names go stale, so they cannot be used as a hard filter.** The colliding pair
is named `mpdm-sam--lee--bea-1`, but that account is now
`deactivateduser000001`: Slack renames deactivated users, and the name keeps the handles
people had when the conversation was created. Narrowing candidates by current handles would
therefore have *excluded the very conversation being asked for*. The name is a fast path,
never the authority.

**Membership does not always disambiguate.** The two colliding conversations have identical
member sets, verified by `conversations.members`. Verification is necessary for correctness
but is not sufficient to separate every collision. A genuine tie is reported as ambiguous
with both IDs, which is the honest answer, rather than resolved by picking one.

The resolver therefore:

1. Resolves each `--with` selector to exactly one user ID via the user cache. More than one
   match is a hard failure with candidates listed (PRD P10).
2. Narrows the cached group DM list by parsed name and member count. Cheap, and takes 215
   down to a handful in the common case.
3. Verifies each survivor with `conversations.members`, comparing against the requested set
   plus self.
4. **If that finds nothing, falls back to a bounded membership scan** over group DMs with a
   matching member count, capped at 60 probes and announced on stderr. This is what makes
   the stale-name case resolvable at all.
5. Exactly one match is success. Zero is exit 5. More than one is exit 4, with both
   conversation IDs and member lists printed so the caller can pick by ID.

The fallback scan measured 8.9 seconds against the live workspace, which is far too slow to
repeat in an agent loop. Member sets are therefore cached to disk (`members-<team>.json`,
24h TTL, safe because Slack group DM membership is immutable). The same query drops to
0.22 seconds on the second invocation.

### D7. Caching is mandatory, not an optimization

Resolving `@joan` requires the user directory. Building it means paginating `users.list`
over the whole workspace. Doing that on every invocation would make a one-second tool a
ten-second tool, and agents invoke these tools in loops.

- Location: `$SLACKLINE_CACHE_DIR`, else `$XDG_CACHE_HOME/slackline`, else
  `~/.cache/slackline`.
- Contents: `users-<team_id>.json`, `conversations-<team_id>.json`,
  `members-<team_id>.json`, and `external-users-<team_id>.json`.
- TTL: 24 hours for users and external users, 1 hour for conversations, 24 hours for
  membership. Conversations churn faster than people do; group DM membership never
  changes at all, because Slack creates a new conversation rather than adding someone to
  an existing one.
- Keyed by team ID so multiple workspaces cannot poison each other.
- `--refresh-cache` forces a rebuild. A cache miss on a specific ID triggers a targeted
  `users.info` rather than a full rebuild, once per ID per process; an ID it cannot name
  renders as itself and never fails the read.
- **External users.** `users.list` returns only this workspace, so people from another
  company in a shared channel (Slack Connect) are not in the user directory. They are
  named, in order of preference, from the `user_profile` and `user_team` Slack attaches
  to their messages, then from that targeted `users.info`, then by ID. Anyone whose team
  is not ours renders by real name and marked, `@Jane Doe (ext)`, because their handle
  belongs to another workspace and no selector here accepts it; JSONL records carry
  `external` and `team`. External users are kept in their own cache file, without email
  addresses, and never enter the user directory, so `users` and `--with` still answer
  for this workspace only. A colleague `users.info` finds (someone who joined after the
  directory was cached) is held for the process and not persisted; the next `users.list`
  rebuild has them.
- Written atomically, via a temp file and `os.replace`, so a killed process cannot leave a
  truncated cache behind.
- Contains directory data only: names, IDs, handles, display names, email addresses,
  archived flags, and group DM membership. **No message content is ever cached.** The
  directory is created owner-only (`0700`) because it names colleagues and their
  addresses. One Slackline did not create (a user-supplied `SLACKLINE_CACHE_DIR`) is left
  as it is, and a looser mode is reported on stderr when the cache is written, the same
  rule the audit log follows (D20). Like the audit log, the cache never writes through a
  symlink: a link at the file's name is refused with a warning, and the temp file is
  created exclusively.

### D8. Ambiguity is always a hard failure

Uniform across every selector: user, channel, conversation. Never a "best match", never a
first-hit-wins, never a warning that scrolls past. Exit 4, candidates on stderr, nothing
on stdout.

The reasoning is about the future more than the present. A read tool that guesses wrong
wastes tokens. The same resolver behind `slackline-write post` that guesses wrong sends a
message to the wrong people. Building the strict habit while the stakes are low is the
only way it is still strict when they are not.

**One carve-out, added 2026-08-29: archived namesakes.** Slack lets a name be reused once
the old channel is archived, and creates a new group DM rather than reviving an archived
one with the same people. A live conversation and its archived predecessor are therefore
the ordinary shape, not a genuine ambiguity, and reporting them as a tie would make
perfectly ordinary names unusable. The resolver prefers live rows over archived ones for
both name and member-set selection. It is not silent about it: when a name or a member
set also matched archived conversations, they are announced on stderr with their IDs, and
an archived conversation is always readable by `--conversation ID`. A candidate whose
membership Slack cannot report (`channel_not_found`) is skipped with a note rather than
failing a resolution a live row satisfies. When only archived rows match,
they are returned. Two live matches remain a hard failure.

---

## 5. Rendering, the core of the product

### D9. A message is rendered from every source it has, in priority order

18% of real messages have no `text`. Reading `message["text"]` and stopping silently drops
them (PRD section 7). The renderer therefore assembles content from all available sources:

1. `text`, when present, with markup resolved (D10).
2. `blocks`, walked recursively and flattened. `rich_text` blocks carry the real content
   of most modern messages. Section, header, context, and list blocks all become text.
3. `attachments`, rendered as a labeled block with title, text, and fallback.
4. `files`, summarized as name, type, and size rather than dropped.
5. If all of the above are empty, an explicit `[no renderable content: subtype=X]`
   placeholder rather than a blank line.

Point 5 matters as much as the rest. If Slackline cannot render something, it says so
in-band. The model then knows a message existed and was unreadable, which is recoverable.
A silently blank line is not.

### D10. mrkdwn is resolved into readable text

| Wire format | Rendered |
|---|---|
| `<@U0ABC1234>` | `@sam` |
| `<#C0ABC1234\|announcements>` | `#announcements` |
| `<#C0ABC1234>` | `#announcements`, via cache |
| `<https://example.com\|the doc>` | `the doc (https://example.com)` |
| `<https://example.com>` | `https://example.com` |
| `<!here>`, `<!channel>` | `@here`, `@channel` |
| `<!subteam^S123\|@eng>` | `@eng` |
| `<mailto:a@b.com\|a@b.com>` | `a@b.com` |
| `&amp;` `&lt;` `&gt;` | `&` `<` `>` |

Slack HTML-escapes those three entities and only those three, so unescaping is safe and
must happen after markup parsing, never before, or a literal `&lt;@U1&gt;` in someone's
message would be misparsed as a mention.

Unresolvable user IDs render as `@U0ABC1234` rather than failing. A deleted user should
not break a transcript.

### D11. Noise is filtered by default, and the filter is visible

`channel_join` and `channel_leave` were 9% of sampled messages and carry no information a
summarizing model wants. Default: excluded. `--include-system` restores them.

Bot messages are **included** by default. Deploy notifications and CI results are often
exactly what the reader is looking for. `--exclude-bots` removes them.

`tombstone` (deleted) and `retention_threshold` messages render as explicit one-line
markers, because their absence is itself information.

Whenever anything is filtered, the count appears in the trailing metadata record. The
reader always knows the transcript is a subset and by how much.

### D12. Two first-class formats, chosen explicitly

`--format text` is the default, always, for every caller. `--format jsonl` is opt-in.
`--format json` and `--format raw` exist for scripts and debugging respectively.

Slackline never inspects `isatty()` to choose (PRD P15). A harness that allocates a pty
would otherwise silently change the contract, which is the kind of bug that takes a day to
find. The default is `text` rather than `jsonl` because the transcript is both what a
human wants and, measurably, the cheaper representation for a model to read.

The seed proposal suggested TTY-dependent defaults. Rejected on those grounds.

### D13. JSONL records carry raw `ts` alongside ISO time

```json
{"type":"message","conversation_id":"G012ABC","ts":"1756312472.123456",
 "time":"2026-08-26T09:14:32-07:00","user":{"id":"U01","name":"joan","display":"Joan Archer"},
 "text":"...","thread_ts":null,"reply_count":0,"reactions":[{"name":"+1","count":2}],
 "edited":false,"subtype":null,"permalink":null}
```

Slack's `ts` is the message identifier and the thread key. Dropping it, as the seed
proposal's example did, makes `thread --ts` and any cursor-based continuation impossible.
Both representations ship: `ts` for machines, `time` for humans and for time reasoning.

---

## 6. Retrieval

### D14. Time windows convert to Slack epoch bounds

`conversations.history` takes `oldest` and `latest` as Unix timestamps with fractional
seconds. `timeparse.py` converts every accepted form into that pair.

- Relative (`48h`, `7d`, `30m`, `2w`) resolve against the local clock at invocation.
- Absolute values require ISO 8601. A bare date means local midnight. A naive timestamp is
  interpreted in `--timezone`, defaulting to the system zone.
- Output is sorted oldest to newest by default, because that is the order conversations
  are understood in.

### D15. Bounded by default, unbounded only on request

| Setting | Default | Rationale |
|---|---|---|
| `--limit` | 200 | Slack's recommended page size |
| `--max-messages` | 500 | Ceiling across all pages |
| `--all-pages` | off | Paging past the ceiling is a deliberate act |
| `--threads` | `none` | Thread expansion multiplies API calls |
| `--timeout` | 30s | Per request |
| `MAX_PAGES` (no flag) | 100 pages per fetch | A server that keeps returning a cursor with nothing behind it must not hold the process; the same intermediary D17 caps `Retry-After` for |

A read that hits the ceiling is truncated, and says so, loudly, in the trailing record and
on stderr. It is never silently cut short.

`--max-messages` counts top-level messages, and for `thread` it counts the root: Slack's
`conversations.replies` returns the parent in addition to `limit` replies, so the first
request asks for one fewer and the ceiling stays literal, except at 1, where a page of
zero cannot be requested and the result is the root plus one reply. Replies inlined by
`--threads full` do not count toward it: without `--all-pages` each expanded thread
contributes at most one page of `--limit` replies and is counted as incomplete if it had
more; `--all-pages` removes that per-thread bound entirely, which is what asking for
every page means. The worst case under `--all-pages --threads full` is therefore the
product of two ceilings, `MAX_PAGES` pages of roots each with up to `MAX_PAGES` pages of
replies, bounded but large; the directory and membership loops in `api.paginate` carry
their own ceiling and announce it when hit.

### D16. Continuation is reported in the payload, and exit stays 0

A successful but incomplete read exits **0**, with a trailing record:

```json
{"type":"page","conversation_id":"G012ABC","returned":500,"filtered":3,
 "incomplete_threads":0,"has_more":true,"next_cursor":"dXNlcjpVMDYx...",
 "truncated_by":"max-messages"}
```

`truncated_by` is `"max-messages"`, `"max-pages"` (the fetch hit the page ceiling in D15
with more available), or `null`. `has_more` is true exactly when `next_cursor` is usable.
The record is built by one function, `transcript.page_record`, for both `conversation`
and `thread`; `thread` adds `thread_ts`, the root of the thread that was read.

The seed proposal assigned exit code 6 to partial results. Rejected, and this is the
correction I feel most strongly about. A non-zero exit from a call that succeeded is a
lie told to every layer above it: `set -e` aborts the script, CI marks the step failed,
and most agent harnesses treat non-zero as an error worth retrying or escalating. The
retrieval worked. The exit code should say so, and the payload should carry the nuance.
Exit codes are for failures.

### D17. Retry policy

Retry on HTTP 429 and on 5xx. Honor `Retry-After` exactly when present, up to a ceiling of
five minutes, and refuse beyond it with a readable error, so an intermediary cannot park
an agent indefinitely with one header. Otherwise exponential backoff from 1s, capped at
32s, maximum 5 attempts, with jitter. Every wait is announced on stderr so a stalled agent
run is diagnosable rather than mysterious.

Measured on 2026-08-27: four consecutive `conversations.history` calls at `limit=100`
returned in about 0.22s each with no throttling and no rate-limit headers. The 2025
tightening of `conversations.history` for non-Marketplace apps does not appear to apply to
this token. The retry logic ships anyway. That measurement describes one app on one day,
and a tool that only works when the API is healthy is not finished.

---

## 7. Interface contract

### D18. Exit codes

| Code | Meaning |
|---|---|
| 0 | Success, including a successful but truncated or paginated read |
| 1 | Unexpected failure or Slack API error |
| 2 | Invalid arguments |
| 3 | Authentication failure or missing scope |
| 4 | Ambiguous selector |
| 5 | Not found, or not accessible to this user |
| 7 | Refused by the read-only method allowlist |
| 130 | Interrupted, by shell convention, so a harness can tell it from a failure |

6 is deliberately unassigned, so that the seed proposal's meaning cannot be reintroduced
by accident.

### D19. Stream discipline

stdout carries requested data and nothing else: no progress, no warnings, no banners, not
even on success. stderr carries everything else. `slackline-read ... > out.jsonl` must
produce a file containing only valid records (PRD P11).

Records are rendered once, at the end of a successful command, so a failure part way
through leaves stdout empty rather than half-written. JSONL is therefore streamable to
consume but not produced incrementally; the output is bounded by `--max-messages`.

### D20. Local audit log

One JSON line per invocation to `~/.local/state/slackline/audit.jsonl`: timestamp,
command, argv with any token-shaped string scrubbed, resolved conversation IDs, time
window, message count, duration, exit code. **Never message content.** Disabled by setting
`SLACKLINE_AUDIT_LOG=none`. Failure to write the audit log is a warning, never a failure
of the command. The file is created owner-only (`0600`), and the directory the log lives
in is `0700` when Slackline created it, for the same reason the cache directory is: the
log names the conversations read and carries argv. A directory Slackline did not create is left alone.
The path may not be a symlink: a link is refused with a warning and nothing is written,
because a link planted in a shared directory would otherwise make a read-only tool append
to any file the user can write. The same goes for a FIFO, a hardlink, or a file another
account created: only a regular file with one link that this account owns is written,
and anything else is refused with a warning. Keep the log in a directory only you can
write.

---

## 8. Testing

### D21. Fixtures, not the network

Every test runs offline against fixtures in `tests/fixtures/`, so the suite is fast,
deterministic, runnable in CI with no secrets, and cannot be defeated by workspace state
changing underneath it.

Fixtures are **synthesized to match observed shapes**, not recorded verbatim. The
reconnaissance told us the distribution of subtypes, block structures, and edge cases; the
fixtures reproduce those shapes with invented content. This keeps real message content and
real colleague names out of a git repository permanently, which matters more here than
fixture realism does.

The fixture set explicitly covers the cases the survey found: empty `text` with `blocks`,
`rich_text` nesting, attachments without text, file shares, all observed subtypes,
messages with reactions, edited messages, thread parents and replies, unresolvable user
IDs, and colliding group DM names.

### D22. A live smoke test, kept separate

`tests/live/` holds a small read-only end-to-end check that runs only when
`SLACKLINE_LIVE_TEST=1` and a token are both present. Never part of the default suite,
never in CI. It exists to catch the class of bug fixtures cannot: Slack changing its wire
format.

---

## 9. Rejected alternatives

| Considered | Rejected because |
|---|---|
| MCP server | The permission boundary stops being an OS-level fact. A running server can also drift from the version you think you deployed. |
| Single `slackline` binary | Subcommand allowlisting is weaker than PATH-level separation, and unavailable in most sandboxes. |
| Slack SDK | Adds a dependency tree, obscures the wire format, and makes the D3 allowlist harder to enforce. |
| `httpx` | Nothing here needs it. Zero dependencies is worth more than ergonomics in a tool agents must install unattended. |
| SQLite cache | Two JSON files with atomic replacement do the job. A schema and a migration story would be pure cost. |
| Exit 6 for partial results | See D16. Non-zero on success breaks every consumer above it. |
| TTY-dependent format defaults | See D12. Environment-dependent contracts produce bugs that are very hard to trace. |
| `conversations.open` for DM resolution | Can create conversations. Violates the read-only guarantee. See D6. |
| Caching message content | Turns a stateless tool into a stale datastore, and puts Slack content on disk. Warehousing Slack history is a different tool's job. |
