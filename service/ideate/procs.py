"""Finding and stopping the processes a harness run started.

Pi runs in its own process group, but what it launches (a dev server from bash, a check of
ideate-loop, anything that calls setsid) may not be in that group, and once its parent dies it
is re-parented to launchd and can no longer be traced. So the runner keeps a record of the
descendants it has seen, as (pid, start time) pairs, and sweeps exactly those when the run
ends or is stopped. A pid whose start time no longer matches was reused by something else and
is left alone: only processes seen as descendants of this run are ever signalled.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time

PS = ("/bin/ps", "-A", "-o", "pid=,ppid=,lstart=")


def _rows() -> list[tuple[int, int, str]]:
    """(pid, ppid, start time) for every process, or [] when ps can't answer."""
    try:
        out = subprocess.run(PS, capture_output=True, text=True, timeout=5, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            rows.append((int(parts[0]), int(parts[1]), " ".join(parts[2].split())))
    return rows


def descendants(root_pid: int) -> dict[int, str]:
    """pid -> start time for every live descendant of root_pid (not root itself)."""
    rows = _rows()
    children: dict[int, list[tuple[int, str]]] = {}
    for pid, ppid, started in rows:
        children.setdefault(ppid, []).append((pid, started))
    found: dict[int, str] = {}
    stack = [root_pid]
    while stack:
        for pid, started in children.get(stack.pop(), []):
            if pid not in found and pid != root_pid:
                found[pid] = started
                stack.append(pid)
    return found


def remember(known: dict[int, str], root_pid: int) -> None:
    """Add root_pid's current descendants to `known`."""
    known.update(descendants(root_pid))


def sweep(known: dict[int, str], *, grace: float = 1.0) -> int:
    """SIGTERM then SIGKILL every still-running process in `known` that is the same process
    we saw (same pid and start time). Returns how many were signalled."""
    me = os.getpid()
    alive = {pid: started for pid, _ppid, started in _rows()}
    targets = [pid for pid, started in known.items()
               if pid > 1 and pid != me and alive.get(pid) == started]
    count = len(targets)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if not targets:
            break
        for pid in targets:
            try:
                os.kill(pid, sig)
            except OSError:
                pass
        if sig == signal.SIGTERM:
            end = time.monotonic() + grace
            while time.monotonic() < end:
                time.sleep(0.05)
                alive = {pid: started for pid, _ppid, started in _rows()}
                targets = [pid for pid in targets if alive.get(pid) == known[pid]]
                if not targets:
                    break
    return count
