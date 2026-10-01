"""The slackline skill cannot drift from the tool it wraps.

The skill is the discovery layer: it tells an agent that slackline-read exists
and takes precedence for Slack reads. A skill that names a flag or command the
CLI does not have would steer agents wrong with total confidence, so every flag
and command it mentions is checked against the CLIs' own help; the reverse
direction is pinned only for the unshipped surface and the exit-code set, since
the skill is a guide, not an enumeration. Known ceiling: flags are checked
against the union of every command's help, not per command, so a valid flag on
the wrong command would pass here and exit 2 at runtime.
"""
from __future__ import annotations

import pathlib
import re

from slackline import cli_read

SKILL = pathlib.Path(__file__).parent.parent / ".claude" / "skills" / "slackline" / "SKILL.md"


def skill_text():
    return SKILL.read_text()


def code_spans(text):
    """Where slackline invocations live: fenced blocks that invoke
    slackline-read, plus every inline backtick span. A fenced block that does
    not mention slackline-read (the user-relaunch command, say) is someone
    else's CLI and its flags are not ours to check."""
    fenced = [f for f in re.findall(r"```.*?```", text, flags=re.S)
              if "slackline-read" in f]
    inline = re.findall(r"`[^`\n]+`", text)
    return "\n".join(fenced + inline)


def help_corpus():
    parts = [cli_read.USAGE, cli_read.EXIT_CODES]
    for name in cli_read.COMMANDS:
        parts.append(cli_read._load_command(name).HELP)
    return "\n".join(parts)


def test_the_skill_exists_with_frontmatter():
    text = skill_text()
    assert text.startswith("---\n")
    frontmatter = text.split("---", 2)[1]
    assert re.search(r"^name: slackline$", frontmatter, flags=re.M)
    description = re.search(r"^description: (.+)$", frontmatter, flags=re.M)
    assert description and len(description.group(1).strip()) > 40


def test_every_flag_the_skill_names_exists():
    corpus = help_corpus()
    flags = set(re.findall(r"--[a-z][a-z-]*", code_spans(skill_text())))
    assert flags, "the skill should demonstrate real invocations"
    # Token boundary, not substring: "--thread" must not ride on "--threads".
    missing = sorted(f for f in flags if not re.search(re.escape(f) + r"\b", corpus))
    assert not missing, f"the skill names flags the CLI does not have: {missing}"


def test_every_command_the_skill_names_exists():
    invoked = set(re.findall(r"slackline-read\s+([a-z][a-z]*)", code_spans(skill_text())))
    assert invoked, "the skill should demonstrate real commands"
    unknown = sorted(c for c in invoked if c not in cli_read.COMMANDS)
    assert not unknown, f"the skill names commands the CLI does not have: {unknown}"


def test_the_skill_states_the_precedence_and_the_credential_stance():
    text = skill_text()
    assert "takes precedence" in text
    assert "never run `op`" in text
    assert "never touch a keychain" in text
    assert "SLACK_USER_TOKEN" in text
    assert "Do not hunt for credentials" in text


def test_the_skill_does_not_document_unshipped_surface():
    """The skill describes the tool as it is, not as planned. Flags and exit
    codes arrive in the skill in the same PR that ships them."""
    text = skill_text()
    assert "--include-permalinks" not in text
    assert "--policy" not in text


def test_the_skill_lists_exactly_the_shipped_exit_codes():
    """The skill's exit-code set equals the CLI's. A code the CLI gains (8 is
    next, with the policy PR) or loses must arrive in or leave the skill in the
    same PR that ships the change."""
    body = skill_text()
    section = body.split("## Exit codes", 1)[1].split("##", 1)[0]
    skill_codes = {int(c) for c in re.findall(r"^- `(\d+)`", section, flags=re.M)}
    cli_codes = {int(c) for c in re.findall(r"^\s*(\d+)\s", cli_read.EXIT_CODES, flags=re.M)}
    assert skill_codes == cli_codes
