"""A skill tool's process (and anything it started) stops with the turn: on cancel and
on timeout. Synthetic script in a temp folder; no real skill runs."""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from service.skills import tools as skill_tools


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


# A child that hangs with the shell alone: Simulation QA's sandbox refuses to run
# /bin/sleep, and /dev/fd (process substitution), and macOS's bash 3.2 has no coproc.
# A builtin loop in a backgrounded subshell (a fork of bash, no exec) runs until it is
# killed; each test kills it within about a second.
def _script(folder: Path) -> Path:
    (folder / "run.sh").write_text(
        "#!/bin/bash\n( while :; do :; done ) &\necho $! > child.pid\necho $$ > parent.pid\nwait\n")
    return folder


def _wait_for(path: Path) -> int:
    deadline = time.time() + 10
    while time.time() < deadline:
        if path.exists() and path.read_text().strip():
            return int(path.read_text())
        time.sleep(0.05)
    raise AssertionError(f"{path.name} never appeared")


def _gone(*pids: int) -> bool:
    deadline = time.time() + 5
    while time.time() < deadline:
        if not any(_alive(p) for p in pids):
            return True
        time.sleep(0.05)
    return False


def test_cancelled_turn_kills_the_tool_and_its_children(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_tools.sandbox, "available", lambda: False)
    run = skill_tools._make_runner(_script(tmp_path), {"script": "run.sh"}, "synthetic_stop")

    async def go():
        task = asyncio.create_task(run())
        child = await asyncio.to_thread(_wait_for, tmp_path / "child.pid")
        parent = await asyncio.to_thread(_wait_for, tmp_path / "parent.pid")
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return parent, child

    parent, child = asyncio.run(go())
    assert _gone(parent, child)


def test_timeout_kills_the_tool_and_its_children(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_tools.sandbox, "available", lambda: False)
    monkeypatch.setattr(skill_tools, "_TIMEOUT_S", 1)
    run = skill_tools._make_runner(_script(tmp_path), {"script": "run.sh"}, "synthetic_timeout")
    out = asyncio.run(run())
    assert "timed out" in out
    assert _gone(int((tmp_path / "parent.pid").read_text()), int((tmp_path / "child.pid").read_text()))
