"""Candidate review #2: more places whose files run as code later ask under Full
access: user-writable PATH folders, .pth files and sitecustomize in site-packages,
~/Library/Services, Folder Action Scripts and editor startup files. And write_file
doesn't keep the execute bit of a file it overwrites, so overwriting a script can't
leave runnable code behind. A temp HOME and invented paths only.
"""
from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

import pytest

_scratch = tempfile.TemporaryDirectory(prefix="harbor-code-run-")
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


@pytest.mark.parametrize("target", [
    "~/.local/bin/fixture-tool", "~/bin/fixture-tool", "/opt/homebrew/bin/fixture-tool",
    "/usr/local/bin/fixture-tool",
    "~/Library/Python/3.14/lib/python/site-packages/fixture.pth",
    "~/Documents/venv/lib/python3.14/site-packages/fixture.pth",
    "~/Documents/venv/lib/python3.14/site-packages/sitecustomize.py",
    "~/Library/Services/Fixture.workflow/Contents/document.wflow",
    "~/Library/Scripts/Folder Action Scripts/fixture.scpt",
    "~/.vimrc", "~/.vim/plugin/fixture.vim", "~/.config/nvim/init.lua", "~/.emacs",
    "~/.emacs.d/init.el",
])
def test_each_asks_under_full_access(target):
    decision = policy.decide("fs_write", {"path": target, "content": "x"}, tool="write_file")
    assert decision.tier is policy.Tier.CONFIRM, (target, decision.reason)


def test_an_ordinary_python_file_is_still_allowed():
    decision = policy.decide("fs_write", {"path": "~/Documents/venv/fixture_notes.py", "content": "x"},
                             tool="write_file")
    assert decision.tier is policy.Tier.ALLOW


def test_write_file_drops_the_execute_bit_of_a_file_it_overwrites(tmp_path):
    from service.tools.builtin import write_file
    script = tmp_path / "fixture.sh"
    script.write_text("echo fixture\n")
    os.chmod(script, 0o755)
    write_file(str(script), "echo replaced\n")
    assert script.read_text() == "echo replaced\n"
    assert not script.stat().st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
