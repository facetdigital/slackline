"""The package exists, is importable, and its entry point runs."""
from __future__ import annotations

import subprocess
import sys


def test_package_exposes_a_version():
    import slackline

    assert isinstance(slackline.__version__, str)
    assert slackline.__version__


def test_entry_point_reports_version_and_exits_zero():
    result = subprocess.run(
        [sys.executable, "-m", "slackline.cli_read", "--version"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "slackline-read" in result.stdout


def test_entry_point_help_exits_zero():
    result = subprocess.run(
        [sys.executable, "-m", "slackline.cli_read", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "read-only" in result.stdout.lower()


def test_no_command_is_an_argument_error():
    """Exit 2 for invalid arguments, per architecture decision D18."""
    result = subprocess.run(
        [sys.executable, "-m", "slackline.cli_read"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert result.stdout == "", "usage errors must not pollute stdout (D19)"


def test_no_source_file_contains_merge_conflict_markers():
    """A conflict resolved by committing the markers is a real thing that happens.

    Python will happily import a file whose conflict lives inside a string or a
    comment block, so this is not always caught by the suite failing.
    """
    import pathlib

    import slackline

    root = pathlib.Path(slackline.__file__).parent.parent.parent
    markers = ("<" * 7, "=" * 7, ">" * 7)
    offenders = []
    for path in list(root.rglob("*.py")) + list(root.rglob("*.md")):
        if ".venv" in path.parts or ".git" in path.parts:
            continue
        text = path.read_text(errors="replace")
        for line in text.splitlines():
            if any(line.startswith(marker) for marker in markers):
                offenders.append(f"{path.relative_to(root)}: {line[:40]}")
    assert not offenders, "unresolved merge conflict markers:\n  " + "\n  ".join(offenders)


def test_the_version_is_the_same_everywhere():
    """A pin, not a bug test: both version declarations exist today and agree.
    This keeps pyproject.toml and slackline.__version__ from ever drifting."""
    import pathlib
    import re

    import slackline

    pyproject = (pathlib.Path(slackline.__file__).parent.parent.parent / "pyproject.toml").read_text()
    declared = re.search(r'^version = "([^"]+)"$', pyproject, flags=re.M).group(1)
    assert declared == slackline.__version__


def test_the_version_is_tracked_in_all_four_places():
    """VERSION is the number of record; pyproject, the package, and the skill
    release together under one version (see CLAUDE.md, Versioning). Installed
    copies of the skill may drift from an installed binary by design; the
    sources may not."""
    import pathlib
    import re

    import slackline

    root = pathlib.Path(slackline.__file__).parent.parent.parent
    on_record = (root / "VERSION").read_text().strip()
    declared = re.search(r'^version = "([^"]+)"$', (root / "pyproject.toml").read_text(),
                         flags=re.M).group(1)
    skill = (root / ".claude" / "skills" / "slackline" / "SKILL.md").read_text()
    frontmatter = skill.split("---", 2)[1]
    skill_version = re.search(r"^version: (.+)$", frontmatter, flags=re.M).group(1).strip()
    assert on_record == declared == slackline.__version__ == skill_version
