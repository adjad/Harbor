"""Candidate review #3: under Full access, with legacy routing, a shell command that
writes a shell startup file, a LaunchAgent or another place whose files run as code
later asks first, like the file tools do. Reading them and ordinary commands don't.
Invented commands only; nothing runs.
"""
from __future__ import annotations

import pytest

from service.safety import grants, policy


@pytest.fixture(autouse=True)
def full_access(monkeypatch, tmp_path):
    monkeypatch.setattr(policy, "_FULL_ACCESS", True)
    monkeypatch.setattr(grants, "GRANTS_PATH", tmp_path / "grants.json")
    monkeypatch.setattr(grants, "_CACHE", {})


@pytest.mark.parametrize("cmd", [
    "echo 'export FIXTURE_FLAG=1' >> ~/.zshrc",
    "printf x >> $HOME/.bash_profile",
    "cp ~/Downloads/fixture.plist ~/Library/LaunchAgents/",
    "mv /tmp/fixture.plist \"$HOME/Library/LaunchAgents/com.example.fixture.plist\"",
    "cd ~ && echo x >> .zprofile",
    "git config --global core.hooksPath /tmp/fixture-hooks",
    "echo x > ~/Documents/fixture-repo/.git/hooks/pre-commit",
    "echo 'import os' > ~/venv/lib/python3.14/site-packages/fixture.pth",
    "cp fixture-tool ~/.local/bin/",
    "ln -s /tmp/fixture /opt/homebrew/bin/fixture",
    "launchctl load ~/Library/LaunchAgents/com.example.fixture.plist",
])
def test_it_asks(cmd):
    decision = policy.decide("shell", {"cmd": cmd}, tool="run_shell")
    assert decision.tier is policy.Tier.CONFIRM, (cmd, decision.reason)


def test_a_standing_grant_cannot_lift_it():
    grants._CACHE["run_shell"] = {"allow": [{"scope": "echo"}], "deny": []}
    assert policy.decide("shell", {"cmd": "echo x >> ~/.zshrc"}, tool="run_shell").tier is policy.Tier.CONFIRM


@pytest.mark.parametrize("cmd", ["ls ~/Library/LaunchAgents", "cat ~/.zshrc",
                                 "/opt/homebrew/bin/python3 --version", "echo fixture > ~/Documents/notes.txt"])
def test_reading_and_ordinary_commands_are_unchanged(cmd):
    assert policy.decide("shell", {"cmd": cmd}, tool="run_shell").tier is policy.Tier.ALLOW, cmd
