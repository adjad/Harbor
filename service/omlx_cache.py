"""Keep oMLX's on-disk prompt cache from growing without bound.

oMLX writes a near-full snapshot of every ~11k-token prompt, so Harbor fills a
multi-GB cache within minutes. Its own default ("auto") reserves half of the
free disk and it has no age-based expiry, so entries for retired models or
prompts sit there until the cap forces them out. Two best-effort controls:

* `ensure_cap` lowers `cache.ssd_cache_max_size` in oMLX's settings.json. oMLX
  reads that file at startup, so it takes effect on the next engine start. A
  smaller value the user chose is left alone.
* `sweep_stale` deletes cache files not touched for `MAX_AGE_DAYS`. oMLX
  treats a vanished block file as a cache miss, so this is safe while it runs.
* `trim_vision` keeps the image-feature cache under `VISION_CAP_BYTES`. oMLX
  hardcodes that cache at 10GB with no setting, so Harbor trims it itself,
  oldest-used first, at startup and every `INTERVAL_S` while running.

The cache is recomputable state, so the worst outcome of either is one slow
first turn.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path

from service.config import _OMLX_STATE, OMLX_SETTINGS

# One cold Harbor prompt writes 0.5-0.8 GB of blocks, so 2 GB still holds the
# Ask, Brief and digest prefixes; 4 GB only cost users disk.
CAP_BYTES = 2 * 1024**3
CAP_TEXT = "2GB"
MAX_AGE_DAYS = 7
VISION_CAP_BYTES = 256 * 1024**2
INTERVAL_S = 6 * 3600
# Unfinished writes are `.tmp`; oMLX keeps recent ones for another manager.
_TMP_GRACE_S = 24 * 3600

_UNITS = {"": 1, "B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}


def _parse_size(text: str) -> int | None:
    m = re.fullmatch(r"\s*([\d.]+)\s*([KMGT]?B?)\s*", str(text).upper())
    if not m or m.group(2) not in _UNITS:
        return None
    return int(float(m.group(1)) * _UNITS[m.group(2)])


def cache_dir(settings: dict | None = None) -> Path:
    configured = ((settings or {}).get("cache") or {}).get("ssd_cache_dir")
    return Path(configured).expanduser() if configured else _OMLX_STATE / "cache"


def ensure_cap(path: Path = OMLX_SETTINGS) -> bool:
    """Lower the SSD cache cap to CAP_TEXT. True when the file was changed."""
    try:
        data = json.loads(path.read_text())
        cache = data.setdefault("cache", {})
        current = cache.get("ssd_cache_max_size", "auto")
        size = None if str(current).lower() == "auto" else _parse_size(current)
        if size is not None and size <= CAP_BYTES:
            return False
        cache["ssd_cache_max_size"] = CAP_TEXT
        _write_private(path, json.dumps(data, indent=2))
        return True
    except (OSError, ValueError):
        return False


# oMLX's paged SSD cache (omlx/cache/paged_ssd_cache.py): one block per
# <hash>.safetensors in a one-hex-character folder, unfinished writes as
# <hash>_tmp_<8 hex>.safetensors. Only those are ever swept, so a cache folder
# pointed at a shared place never loses anything else.
_BLOCK = re.compile(r"[0-9a-f]{16,128}(_tmp_[0-9a-f]{8})?\.safetensors")
_BUCKET = re.compile(r"[0-9a-f]")


def _write_private(path: Path, text: str) -> None:
    """Replace `path` through a temp file that is 0600 from the moment it exists.

    settings.json holds the engine's API key: the temp file used to take the
    umask mode (0644) until a chmod after the write, and a failure between the
    two left a readable copy behind (pentest #15). A leftover or planted temp
    name is removed first and never followed.
    """
    tmp = path.with_name(path.name + ".harbor-tmp")
    try:
        tmp.unlink()
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        try:
            os.fchmod(fd, 0o600)
            data = text.encode("utf-8")
            while data:
                data = data[os.write(fd, data):]
        finally:
            os.close(fd)
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def sweep_stale(root: Path | None = None, *, now: float | None = None,
                max_age_days: float = MAX_AGE_DAYS) -> tuple[int, int]:
    """Delete oMLX cache block files untouched for `max_age_days`. Returns (files, bytes)."""
    if root is None:
        try:
            settings = json.loads(OMLX_SETTINGS.read_text())
        except (OSError, ValueError):
            settings = None
        root = cache_dir(settings)
    root = Path(root)
    if not root.is_absolute():
        return 0, 0          # relative to the backend's cwd: not a place to delete from
    now = time.time() if now is None else now
    cutoff = now - max_age_days * 86400
    files = freed = 0
    try:
        buckets = [root] + [d for d in root.iterdir() if _BUCKET.fullmatch(d.name) and d.is_dir()
                            and not d.is_symlink()]
    except OSError:
        return 0, 0
    for folder in buckets:
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            match = _BLOCK.fullmatch(name)
            if not match:
                continue
            p = folder / name
            try:
                st = p.lstat()
                if not p.is_file() or p.is_symlink():
                    continue
                fresh = max(st.st_mtime, st.st_atime)
                if fresh > (now - _TMP_GRACE_S if match.group(1) else cutoff):
                    continue
                p.unlink()
            except OSError:
                continue
            files += 1
            freed += st.st_size
    return files, freed


def trim_vision(root: Path | None = None, *, max_bytes: int = VISION_CAP_BYTES) -> tuple[int, int]:
    """Delete the least recently used vision-feature files until the directory
    fits `max_bytes`. Returns (files, bytes) removed."""
    if root is None:
        try:
            settings = json.loads(OMLX_SETTINGS.read_text())
        except (OSError, ValueError):
            settings = None
        root = cache_dir(settings) / "vision_features"
    entries = []
    for dirpath, _dirs, names in os.walk(root):
        for name in names:
            p = Path(dirpath) / name
            try:
                st = p.lstat()
            except OSError:
                continue
            if p.is_file() and not p.is_symlink():
                entries.append((max(st.st_mtime, st.st_atime), st.st_size, p))
    total = sum(size for _t, size, _p in entries)
    files = freed = 0
    for _t, size, p in sorted(entries, key=lambda e: e[0]):
        if total <= max_bytes:
            break
        try:
            p.unlink()
        except OSError:
            continue
        total -= size
        files += 1
        freed += size
    return files, freed


def maintain() -> None:
    sweep_stale()
    trim_vision()


async def run() -> None:
    """Periodic maintenance for a long-lived backend; startup does its own."""
    while True:
        await asyncio.sleep(INTERVAL_S)
        try:
            await asyncio.to_thread(maintain)
        except Exception:  # noqa: BLE001 — best-effort, never fatal
            pass
