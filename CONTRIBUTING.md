# Contributing to Slackline

Thanks for helping. Slackline is small on purpose, and most of what makes it useful is a
handful of promises it keeps: it never writes to Slack, never leaks a token, never drops a
message silently, and never guesses. This guide gets you a working checkout and explains
the rules that protect those promises.

Before proposing a feature, read [what this is not](README.md#what-this-is-not). Anything
that writes to Slack belongs to the planned `slackline-write`, never to `slackline-read`.
Security problems go through [SECURITY.md](SECURITY.md), not a public issue.

## Dev setup

You need Python 3.9 or newer. CI runs the suite on 3.9 and 3.13.

```bash
git clone https://github.com/facetdigital/slackline.git
cd slackline

# With uv
uv venv .venv && uv pip install -e '.[dev]'

# Or with the standard library
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
```

Run the offline suite with the virtualenv's interpreter:

```bash
.venv/bin/python -m pytest -q          # offline: no network, no secrets, no token
```

On macOS a bare `python3` is usually the system or Xcode interpreter, which has no
`pytest`, and bare `python` is often not on `PATH` at all. Use `.venv/bin/python`, or
`source .venv/bin/activate` first.

The default suite runs entirely against synthetic fixtures. It removes
`SLACK_USER_TOKEN` from the environment and fails any test that reaches for a credential,
so it passes the same on your laptop as in CI.

### Live checks (optional)

A few smoke tests talk to a real workspace with your own token, read-only. They are
excluded from the default run and skip unless you opt in:

```bash
read -rs SLACK_USER_TOKEN && export SLACK_USER_TOKEN
SLACKLINE_LIVE_TEST=1 .venv/bin/python -m pytest tests/live -q
```

For day-to-day development you can keep a token in a local `.env` (see
[.env.example](.env.example)). It is gitignored; never commit a token, and never add a
`--token` flag (a token in `argv` is visible to every process and lands in shell history).

## The rules every change follows

The full list, with the reasoning, is in [CLAUDE.md](CLAUDE.md). It is written for AI
coding agents and is the most complete statement of the rules for people too. In short:

- **Read-only is enforced, not intended.** Every Slack call passes an allowlist of methods
  in `api.py`, and a test fails if a write method is added. Do not widen it.
- **No token reaches output.** Errors, tracebacks, and the audit log are scrubbed.
- **Nothing is silently dropped, and filtering is always counted.** A subset must never
  look like a quiet conversation.
- **Ambiguity is a hard failure.** Never resolve a selector by guessing.
- **stdout carries only requested data.** Diagnostics go to stderr.
- **Zero runtime dependencies.** Adding one is an architecture decision, not a convenience.
- **The audit log and the cache never hold message content.**

Design decisions are numbered D1 and up in
[docs/architecture/architecture.md](docs/architecture/architecture.md). Code comments and
commit messages cite them; please keep doing that.

### Tests first

Write the test expecting correct behavior, run it, and watch it fail against the current
code. Then make it pass without editing the test. A test that passes against the bug
proves nothing. Fixtures are synthetic: no real message content, names, or IDs.

### Docs move with the code

A change in behavior updates the README, the architecture doc if a decision changed, and
the [roadmap](docs/plans/development-roadmap.md), in the same pull request.

## Pull requests

1. Fork, and branch from `main`. One change per pull request.
2. Keep the diff as small as the job. Unrelated cleanup belongs in its own PR.
3. Commit messages explain why, not what the diff already shows.
4. The offline suite must pass on 3.9 and 3.13; CI checks every PR.
5. The maintainer reviews and merges, with full history (merge commits, not squash).

Not sure whether something fits? Open an issue first and ask. The bar is low.

## Cutting a release (maintainers)

The binary and the skill release together under one version. It is declared in
`VERSION` (the number of record), `pyproject.toml`, `src/slackline/__init__.py`, and the
skill's frontmatter, and the README quick start installs the release wheel by a URL that
names it. The suite fails until all of them agree.

1. Bump all of them in one PR, and merge it.
2. Tag the merge commit and push the tag: `git tag -a vX.Y.Z -m "..." && git push origin vX.Y.Z`.
3. Build from a clean export of the tag, and zip the skill:
   ```bash
   mkdir -p /tmp/slackline-X.Y.Z && git archive vX.Y.Z | tar -x -C /tmp/slackline-X.Y.Z
   (cd /tmp/slackline-X.Y.Z && uv build -o dist)
   (cd /tmp/slackline-X.Y.Z/.claude/skills && zip -r -X ../../dist/slackline-skill-X.Y.Z.zip slackline)
   ```
4. Publish the release with the wheel, the source tarball, and the skill zip attached:
   `gh release create vX.Y.Z --verify-tag --notes-file NOTES.md /tmp/slackline-X.Y.Z/dist/*`
