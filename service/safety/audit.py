"""Append-only audit log of every tool action and its outcome."""
from __future__ import annotations

import json
import logging
import os
import stat
import threading
import time
from pathlib import Path

from service.paths import MOE_DIR, private_append_text

AUDIT_DIR = MOE_DIR
AUDIT_LOG = AUDIT_DIR / "audit.jsonl"
_warned = False
# Past this size the log moves to audit.jsonl.1, and older generations shift up
# to audit.jsonl.<KEEP_GENERATIONS>; the oldest is dropped. Several are kept so a
# burst of actions can't push the record of an earlier one out (candidate review #4).
ROTATE_BYTES = 8 * 1024 * 1024
KEEP_GENERATIONS = 5
_rotate_lock = threading.Lock()


def _rotate() -> None:
    """Shift the generations up when the log is past ROTATE_BYTES. Never follows a
    link: renames and unlinks act on the names themselves."""
    try:
        info = os.lstat(AUDIT_LOG)
    except OSError:
        return
    if not stat.S_ISREG(info.st_mode) or info.st_size < ROTATE_BYTES:
        return
    with _rotate_lock:
        try:
            if os.lstat(AUDIT_LOG).st_size < ROTATE_BYTES:
                return                                  # another thread rotated it
            name = AUDIT_LOG.name
            oldest = AUDIT_LOG.with_name(f"{name}.{KEEP_GENERATIONS}")
            try:
                os.unlink(oldest)
            except FileNotFoundError:
                pass
            for n in range(KEEP_GENERATIONS - 1, 0, -1):
                src = AUDIT_LOG.with_name(f"{name}.{n}")
                try:
                    os.replace(src, AUDIT_LOG.with_name(f"{name}.{n + 1}"))
                except FileNotFoundError:
                    pass
            os.replace(AUDIT_LOG, AUDIT_LOG.with_name(f"{name}.1"))
        except OSError:
            pass                                       # keep appending to the current one


def rotated(path: Path) -> Path:
    return path.with_name(path.name + ".1")


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
        _rotate()
        private_append_text(AUDIT_LOG, json.dumps(rec, default=str) + "\n")
    except Exception as exc:  # noqa: BLE001
        global _warned
        if not _warned:
            # e.g. a symlink in its place (refused, never written through).
            _warned = True
            logging.getLogger(__name__).warning("audit log %s not written: %s", AUDIT_LOG, exc)
