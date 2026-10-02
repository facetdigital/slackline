## What and why

<!-- What this changes, and why. Cite architecture decisions (D1, D2, ...) where relevant. -->

## Checklist

- [ ] Tests written first, and seen failing against the code before the change
- [ ] `.venv/bin/python -m pytest -q` passes
- [ ] Read-only allowlist untouched, and no new runtime dependency
- [ ] Docs moved with the code (README, architecture, roadmap) where behavior changed
- [ ] No tokens, real message content, or real names in code, fixtures, or docs
