"""Argument errors exit 2 whether or not a credential exists (D18, invariant 10).

Every case runs from an empty directory with no token in the environment; the
conftest guard fails the test if a credential helper is spawned or ./.env is
read. A command's arguments are validated before any credential is resolved, so
an agent with a bad flag is told "bad argument", never "no token".
"""
from __future__ import annotations

import pytest

from slackline import cli_read


@pytest.fixture(autouse=True)
def empty_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)


ARGUMENT_ERRORS = [
    ["conversation", "--nonsense"],
    ["conversation", "--since", "48h"],                       # no selector
    ["conversation", "--channel", "a", "--with", "@b"],       # conflicting selectors
    ["conversation", "--channel", "a", "--since", "48"],      # bare number
    ["conversation", "--channel", "a", "--between", "2026-08-20", "2026-08-10"],
    ["conversation", "--channel", "a", "--threads", "sideways"],
    ["thread", "--channel", "a"],                             # no --ts
    ["thread", "--channel", "a", "--ts", "1.0", "--since", "1d"],
    ["channels", "--kind", "bogus"],
    ["users", "--bogus"],
    ["resolve", "--bogus"],
    ["resolve"],                                              # no selector
    ["whoami", "--bogus"],
]


@pytest.mark.parametrize("argv", ARGUMENT_ERRORS, ids=lambda a: " ".join(a))
def test_an_argument_error_exits_2_without_a_credential(argv, capsys):
    code = cli_read.main(argv)
    out, err = capsys.readouterr()
    assert code == 2, err
    assert out == "", "usage errors must not pollute stdout (D19)"
    assert err.startswith("slackline-read:")


def test_valid_arguments_then_reach_the_credential_step(capsys, monkeypatch):
    """The order is arguments, then credential: a well-formed call with no
    token is exit 3, proving the argument check ran first and passed."""
    from slackline import auth

    monkeypatch.setattr(auth, "_read_from_keychain", lambda: None)
    code = cli_read.main(["conversation", "--channel", "a", "--since", "48h"])
    out, err = capsys.readouterr()
    assert code == 3
    assert out == ""
    assert "SLACK_USER_TOKEN" in err
