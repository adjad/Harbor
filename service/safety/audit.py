"""Append-only audit log of every tool action and its outcome."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

from service.paths import MOE_DIR, private_append_text

AUDIT_DIR = MOE_DIR
AUDIT_LOG = AUDIT_DIR / "audit.jsonl"
# Rotated once it passes this size: audit.jsonl becomes audit.jsonl.1 (one older
# generation, replaced each time), so the log no longer grows without end (swarm #37).
# Both live in ~/.moe, which /resetharbor erases whole.
MAX_BYTES = 20 * 1024 * 1024
_warned = False
_rotate_lock = threading.Lock()


def rotated(path: Path) -> Path:
    return path.with_name(path.name + ".1")


def _rotate_if_needed(path: Path, max_bytes: int) -> None:
    with _rotate_lock:
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            return
        if info.st_size < max_bytes or not os.path.isfile(path) or os.path.islink(path):
            return
        os.replace(path, rotated(path))


def tail_lines(limit: int, path: Path | None = None, *, block: int = 64 * 1024) -> list[str]:
    """The last `limit` lines of the log (the rotated one too when needed), oldest
    first, read backwards from the end in blocks: never the whole file."""
    path = AUDIT_LOG if path is None else path
    limit = max(int(limit), 0)
    lines: list[str] = []
    for source in (path, rotated(path)):
        if len(lines) >= limit:
            break
        lines = _tail_of(source, limit - len(lines), block) + lines
    return lines[-limit:] if limit else []


def _tail_of(path: Path, limit: int, block: int) -> list[str]:
    if limit <= 0:
        return []
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return []
    try:
        end = os.fstat(fd).st_size
        data = b""
        position = end
        while position > 0 and data.count(b"\n") <= limit:
            step = min(block, position)
            position -= step
            data = os.pread(fd, step, position) + data
    finally:
        os.close(fd)
    text = data.decode("utf-8", errors="replace").splitlines()
    if position > 0 and text:
        text = text[1:]    # the first line read may have started before the block
    return text[-limit:]


def audit(event: str, **fields) -> None:
    """Append one JSON line to the audit log. Never raises.

    Owner-only (0600): each line names a tool and its arguments (recipients,
    reminder titles, reply fields), so another account must not read it."""
    try:
        from service.safety.redaction import scrub_obj
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event, **scrub_obj(fields)}
        _rotate_if_needed(AUDIT_LOG, MAX_BYTES)
        private_append_text(AUDIT_LOG, json.dumps(rec, default=str) + "\n")
    except Exception as exc:  # noqa: BLE001
        global _warned
        if not _warned:
            # e.g. a symlink in its place (refused, never written through).
            _warned = True
            logging.getLogger(__name__).warning("audit log %s not written: %s", AUDIT_LOG, exc)
