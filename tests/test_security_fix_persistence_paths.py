"""#1: under Full access, file tools may not plant code that runs later without a card.

LaunchAgents, shell startup files, git config, Harbor's own folder (skills included)
and the app bundle ask in every mode, for every source and destination a file tool
names, and a standing grant cannot lift it. A temp HOME and invented paths only.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_scratch = tempfile.TemporaryDirectory(prefix="harbor-persist-")
os.environ.setdefault("HARBOR_HOME", str(Path(_scratch.name) / "moe"))

from service.safety import grants, policy  # noqa: E402


@pytest.fixture(autouse=True)
def full_access_home(monkeypatch, tmp_path):
    home = tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(policy, "_FULL_ACCESS", True)
    monkeypatch.setattr(grants, "GRANTS_PATH", tmp_path / "grants.json")
    monkeypatch.setattr(grants, "_CACHE", {})
    return home


ATTACK_TARGETS = [
    "~/Library/LaunchAgents/com.example.fixture.plist",
    "/Library/LaunchAgents/com.example.fixture.plist",
    "~/.zshrc", "~/.zshenv", "~/.zprofile", "~/.bash_profile", "~/.bashrc", "~/.profile",
    "~/.config/fish/config.fish", "~/.gitconfig",
    "~/.moe/skills/fixture/SKILL.md",
]


@pytest.mark.parametrize("target", ATTACK_TARGETS)
def test_write_file_into_a_code_execution_location_asks_under_full_access(target):
    decision = policy.decide("fs_write", {"path": target, "content": "x"}, tool="write_file")
    assert decision.tier is policy.Tier.CONFIRM, (target, decision.reason)


@pytest.mark.parametrize("target", ATTACK_TARGETS)
def test_move_path_into_one_asks_too(target):
    decision = policy.decide("fs_write", {"source": "~/Documents/fixture.txt", "destination": target},
                             tool="move_path")
    assert decision.tier is policy.Tier.CONFIRM, (target, decision.reason)


def test_moving_a_launch_agent_out_asks_too():
    decision = policy.decide("fs_write", {"source": "~/Library/LaunchAgents/com.example.fixture.plist",
                                          "destination": "~/Documents/"}, tool="move_path")
    assert decision.tier is policy.Tier.CONFIRM


def test_a_move_into_the_launch_agents_folder_by_its_name_asks():
    decision = policy.decide("fs_write", {"source": "~/Documents/fixture.plist",
                                          "destination": "~/Library/LaunchAgents/"}, tool="move_path")
    assert decision.tier is policy.Tier.CONFIRM


def test_the_packaged_app_bundle_asks(monkeypatch, tmp_path):
    from service.safety import shell_sandbox
    bundle = tmp_path / "Fixture.app"
    (bundle / "Contents" / "Resources").mkdir(parents=True)
    monkeypatch.setattr(shell_sandbox, "app_bundle", lambda: bundle)
    decision = policy.decide("fs_write", {"path": str(bundle / "Contents/Resources/policy.yaml"),
                                          "content": "full_access: true"}, tool="write_file")
    assert decision.tier is policy.Tier.CONFIRM


def test_a_delete_of_a_shell_startup_file_asks():
    decision = policy.decide("fs_delete", {"path": "~/.zshrc"}, tool="delete_path")
    assert decision.tier is not policy.Tier.ALLOW


def test_a_standing_grant_cannot_lift_it():
    args = {"path": "~/Library/LaunchAgents/com.example.fixture.plist", "content": "x"}
    grants._CACHE["write_file"] = {"allow": [{"scope": ""}], "deny": []}
    assert policy.decide("fs_write", args, tool="write_file").tier is policy.Tier.CONFIRM


def test_an_ordinary_document_is_still_allowed_under_full_access():
    decision = policy.decide("fs_write", {"path": "~/Documents/fixture-notes.txt", "content": "x"},
                             tool="write_file")
    assert decision.tier is policy.Tier.ALLOW


@pytest.mark.parametrize("source,destination", [
    ("~/.config", "~/fixture-cfg-old"),               # move the parent aside
    ("~/fixture-cfg-old", "~/.config"),               # and back
    ("~/Library", "~/fixture-lib-old"),
])
def test_moving_a_folder_that_holds_one_asks(source, destination):
    decision = policy.decide("fs_write", {"source": source, "destination": destination}, tool="move_path")
    assert decision.tier is policy.Tier.CONFIRM, decision.reason


@pytest.mark.parametrize("target", ["~/Documents/fixture-repo/.git/hooks/pre-commit",
                                    "~/Documents/fixture-repo/.git/config"])
def test_a_repositorys_hooks_and_config_ask(target):
    decision = policy.decide("fs_write", {"path": target, "content": "x"}, tool="write_file")
    assert decision.tier is policy.Tier.CONFIRM, decision.reason


def test_moving_a_file_into_home_itself_is_still_ordinary():
    decision = policy.decide("fs_write", {"source": "~/Documents/fixture.txt", "destination": "~/"},
                             tool="move_path")
    assert decision.tier is policy.Tier.ALLOW, decision.reason


@pytest.mark.parametrize("target", [
    "~/Documents/fixture-repo/.git/modules/lib/hooks/post-checkout",
    "~/Documents/fixture-repo/.git/config.worktree",
    "~/Documents/fixture-repo/.git/worktrees/wt/config.worktree",
    "~/Documents/fixture-bare.git/hooks/post-update",
])
def test_other_git_code_locations_ask(target):
    decision = policy.decide("fs_write", {"path": target, "content": "x"}, tool="write_file")
    assert decision.tier is policy.Tier.CONFIRM, decision.reason


@pytest.mark.parametrize("target", ["~/Documents/fixture-repo/.gitignore",
                                    "~/Documents/fixture-repo/.github/workflows/ci.yml",
                                    "~/Documents/fixture-repo/.git/info/exclude"])
def test_ordinary_repository_files_are_still_allowed(target):
    decision = policy.decide("fs_write", {"path": target, "content": "x"}, tool="write_file")
    assert decision.tier is policy.Tier.ALLOW, decision.reason
