"""Opt-in live checks against a real workspace (D22).

Never part of the default suite and never run in CI. These exist to catch the
class of problem fixtures cannot: Slack changing its wire format, and behavior
that only appears at real data volume.

Run with:
    SLACKLINE_LIVE_TEST=1 python -m pytest tests/live -q

The thread check additionally needs SLACKLINE_LIVE_CHANNEL (a channel name or
ID) and SLACKLINE_LIVE_REPLY_TS (the ts of a reply, not a root, in a thread
there); it is skipped when either is missing.

Every call here is read-only.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("SLACKLINE_LIVE_TEST") != "1",
    reason="live tests are opt-in: set SLACKLINE_LIVE_TEST=1",
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def cli(*argv, shell_pipeline=None):
    if shell_pipeline:
        return subprocess.run(shell_pipeline, shell=True, capture_output=True,
                              text=True, cwd=REPO_ROOT)
    return subprocess.run([sys.executable, "-m", "slackline.cli_read", *argv],
                          capture_output=True, text=True, cwd=REPO_ROOT)


def test_whoami_reaches_slack():
    result = cli("whoami", "--format", "json")
    assert result.returncode == 0, result.stderr
    assert '"team"' in result.stdout


def test_a_closed_downstream_pipe_exits_zero_without_a_traceback():
    """The end-to-end form of the BrokenPipeError fix.

    This needs real volume: a pipe buffer is 64KB, so a small payload never
    triggers the failing write. Listing every conversation comfortably exceeds
    it, which is why this check cannot live in the offline suite.
    """
    result = cli(
        shell_pipeline=f"{sys.executable} -m slackline.cli_read channels "
                       f"--format jsonl | head -1"
    )
    assert result.returncode == 0, result.stderr
    assert "BrokenPipeError" not in result.stderr
    assert "Traceback" not in result.stderr


def test_a_reply_ts_reads_its_thread_from_the_root():
    """Slack's conversations.replies answers a reply's ts with that one message.
    The offline fake assumes as much; only the live API can confirm it."""
    channel = os.environ.get("SLACKLINE_LIVE_CHANNEL")
    reply_ts = os.environ.get("SLACKLINE_LIVE_REPLY_TS")
    if not (channel and reply_ts):
        pytest.skip("set SLACKLINE_LIVE_CHANNEL and SLACKLINE_LIVE_REPLY_TS")
    import json

    result = cli("thread", "--channel", channel, "--ts", reply_ts, "--format", "jsonl")
    assert result.returncode == 0, result.stderr
    rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    first = next(r for r in rows if r["type"] == "message")
    assert first["ts"] == first["thread_ts"], "the root must come first"
    assert "names a reply" in result.stderr
