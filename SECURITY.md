# Security policy

## Reporting a vulnerability

Please report security problems privately, not in a public issue. Use GitHub's private
vulnerability reporting: the **Security** tab of this repository, then **Report a
vulnerability**. Include what you ran, what happened, and what you expected.

Reports are handled on a best-effort basis by the maintainer. You will get an
acknowledgement, and a fix or an explanation, as soon as practical.

## Supported versions

Only the latest release receives security fixes.

## What counts

Slackline's security promises are specific, so these are the reports that matter most:

- **Any way for `slackline-read` to change anything in Slack.** It is read-only by
  construction: every request passes an allowlist of Slack methods. A path around that
  allowlist is the most serious class of bug this project can have.
- **Any way for a token to reach output**: stdout, stderr, a traceback, the audit log, or
  the cache.
- **Message content written to disk.** The audit log and the directory cache are meant to
  hold metadata only, never what anyone said.
- **Writing outside the intended files**, for example through a symlink at the audit log
  or cache path.

Slackline acts with the permissions of the user whose token it holds. Reading what that
user can already read is the intended behavior, not a vulnerability.
