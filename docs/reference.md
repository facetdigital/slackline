# Reference

Detail behind the [README](../README.md): how to read the output, what the exit codes mean, and what Slackline keeps on disk.

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
