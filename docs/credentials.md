# Credentials

Slackline uses a **user token** (`xoxp-...`), not a bot token. It reads exactly what you can read, and nothing you cannot. The [quick start](../README.md#quick-start) shows how to create the Slack app that issues it.

There is deliberately **no `--token` flag**. A token in `argv` is readable by any process via `ps` and is written to your shell history.

## Where the token comes from

Resolution order, first hit wins:

| # | Source | For |
|---|--------|-----|
| 1 | `SLACK_USER_TOKEN` environment variable | **Agents.** The only one an agent needs |
| 2 | macOS Keychain, service `slackline` | Humans on a Mac |
| 3 | 1Password, when `SLACKLINE_OP_REF` is set | Humans who want TouchID gating |
| 4 | `./.env` in the current directory | Local development |

`--token-file PATH` (CI, containers) sits outside that chain: when given, it is consulted first, ahead of the environment, because an explicit instruction overrides the ambient credential. A missing file is an error even when `SLACK_USER_TOKEN` is set.

## For an agent

An agent needs only `SLACK_USER_TOKEN` in its environment. How it gets there is up to you; 1Password is one option, not a requirement.

The simplest is the quick start's own: export the token in a shell, then start the agent from that same shell, which inherits it.

```bash
read -rs SLACK_USER_TOKEN && export SLACK_USER_TOKEN   # paste the token; nothing echoes
claude
```

If you keep the token in 1Password, resolve it at launch instead. Replace the account and the `op://` reference with your own:

```bash
SLACK_USER_TOKEN=$(op read --account your.1password.com \
  "op://Vault/Slack User API Token/credential") claude
```

Either way the runner resolves the secret once and passes it in. The agent never fetches a credential, so it never triggers a TouchID prompt and never has a way to obtain a token on its own.

Pass `--token-source env` in unattended jobs. It pins the source and disables the fallback chain, so a missing credential fails loudly instead of blocking on an interactive 1Password prompt that nobody is there to answer.

## For a human

On a Mac, keep the token in the Keychain so you do not have to export it in every shell:

```bash
security add-generic-password -s slackline -a "$USER" -w   # prompts for the token
slackline-read whoami
```
