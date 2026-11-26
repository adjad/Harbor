"""A skill tool's process (and anything it started) stops with the turn: on cancel and
on timeout. Synthetic script in a temp folder; no real skill runs."""
from __future__ import annotations

import asyncio
import ctypes
import ctypes.util
import os
import signal
import struct
import subprocess
import time
from pathlib import Path

import pytest

from service.skills import tools as skill_tools

_libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


# A child that hangs with the shell alone: Simulation QA's sandbox refuses to run
# /bin/sleep, and /dev/fd (process substitution), and macOS's bash 3.2 has no coproc.
# A builtin loop in a backgrounded subshell (a fork of bash, no exec), bounded by bash's
# own $SECONDS so it ends by itself after LOOP_S even if nothing kills it. Each test
# kills it within about a second; the fixture below kills whatever a failed test left.
LOOP_S = 30


def _script(folder: Path) -> Path:
    (folder / "run.sh").write_text(
        "#!/bin/bash\n"
        f"( end=$((SECONDS + {LOOP_S})); while [ $SECONDS -lt $end ]; do :; done ) &\n"
        "echo $! > child.pid\necho $$ > parent.pid\nwait\n")
    return folder


def _started(pid: int) -> float | None:
    """When process `pid` started (sysctl KERN_PROC_PID), or None if there is none."""
    mib = (ctypes.c_int * 4)(1, 14, 1, pid)          # CTL_KERN, KERN_PROC, KERN_PROC_PID
    buf, size = ctypes.create_string_buffer(1024), ctypes.c_size_t(1024)
    if _libc.sysctl(mib, 4, buf, ctypes.byref(size), None, 0) != 0 or size.value == 0:
        return None
    sec, usec = struct.unpack_from("qi", buf.raw, 0)  # kinfo_proc.kp_proc.p_starttime
    (found,) = struct.unpack_from("i", buf.raw, 40)  # kinfo_proc.kp_proc.p_pid
    return sec + usec / 1e6 if found == pid else None


def _kill_leftovers(folder: Path, since: float) -> None:
    """Kill what this test's script left running, and nothing else. A pid in its files is
    ours only while that process started after the test began and no later than the
    file naming it was written; a later process that reused the number never is."""
    for name in ("child.pid", "parent.pid"):
        path = folder / name
        try:
            pid, written = int(path.read_text()), path.stat().st_mtime
        except (OSError, ValueError):
            continue
        started = _started(pid) if pid > 1 else None   # 0 and -1 would signal whole groups
        if started is None or not since - 1 <= started <= written + 1:
            continue
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            continue
        try:
            os.waitpid(pid, os.WNOHANG)               # reaped here only if it is ours; launchd otherwise
        except ChildProcessError:
            pass


@pytest.fixture(autouse=True)
def _no_loop_outlives_the_test(tmp_path):
    since = time.time()
    yield
    _kill_leftovers(tmp_path, since)


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


def test_cleanup_never_kills_a_process_that_reused_a_recorded_pid(tmp_path):
    since = time.time()
    later = subprocess.Popen(["/bin/bash", "-c", "read -t 20 < /dev/zero; true"])   # not ours
    try:
        (tmp_path / "child.pid").write_text(str(later.pid))
        old = since - 60                                # the number was written before it started
        os.utime(tmp_path / "child.pid", (old, old))
        _kill_leftovers(tmp_path, since)
        assert later.poll() is None
        os.utime(tmp_path / "child.pid", None)          # written after it started: ours, so it goes
        _kill_leftovers(tmp_path, since)
        assert later.wait(5) == -signal.SIGKILL
    finally:
        if later.poll() is None:
            later.kill()
            later.wait()

