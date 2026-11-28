"""Which user skills the person approved, and exactly what they approved.

A folder that appears under ~/.moe/skills on its own (written by a tool, a
script or another app) is not a skill the person chose: it loads switched off.
A skill is trusted only when it arrived through Harbor's own paths that the
person drives, Settings' install, the create-tool card, or the person
switching it on in Settings, and the trust is pinned to a fingerprint of every
file in the folder. SKILL.md carries the tool's command, script and sandbox
scopes, so a skill that changes after approval (a tool rewriting its own
SKILL.md to widen its scopes, a new script) loads switched off again until
the person switches it back on (pentest #1 and #2).

The record lives in Harbor's own folder, outside every skill folder, and is
written only from those person-driven paths. The `enabled:` flag that
Settings writes into SKILL.md is left out of the fingerprint, so switching a
skill off and on doesn't count as a change.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import secrets
import stat
import tempfile
import threading
from pathlib import Path

import yaml

from service.paths import MOE_DIR

TRUST_PATH = MOE_DIR / "skill_trust.json"
_LOCK = threading.Lock()
# Kept for callers that only test for a fence; split_frontmatter is the parser.
_FRONTMATTER_RE = re.compile(r"^\ufeff?---[ \t]*\r?\n(.*?)^---[ \t]*\r?$\n?(.*)", re.DOTALL | re.M)
NOT_APPROVED = "added outside Harbor, so it is off until you switch it on in Settings"
CHANGED = "changed since you approved it, so it is off until you switch it on again in Settings"


def split_frontmatter(text: str) -> tuple[str, str] | None:
    """(frontmatter, body) of a SKILL.md, or None when it has no frontmatter.

    The frontmatter opens with a `---` first line (after an optional UTF-8 BOM)
    and closes at the next line that is exactly `---` (trailing blanks allowed).
    It may be empty. A `---` later in the body is the body's own (a rule line),
    and a line like `--- note` or `----` never closes it.
    """
    if text.startswith("\ufeff"):
        text = text[1:]
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n").rstrip(" \t") != "---":
        return None
    for index in range(1, len(lines)):
        if lines[index].rstrip("\r\n").rstrip(" \t") == "---":
            meta = "".join(lines[1:index])
            return meta, "".join(lines[index + 1:])
    return None


def _json_key(key: object) -> str:
    """A mapping key as json.dumps writes it, for any key YAML can produce."""
    if isinstance(key, str):
        return key
    if key is True:
        return "true"
    if key is False:
        return "false"
    if key is None:
        return "null"
    if isinstance(key, (int, float)):
        return json.dumps(key)
    return str(key)


def _canonical(value: object) -> object:
    """`value` with every mapping key a string, so json.dumps(sort_keys=True) never
    fails on YAML's mixed keys ({1: a, b: c}). All-string mappings are unchanged, so
    an approval recorded before this keeps matching."""
    if isinstance(value, dict):
        out: dict[str, object] = {}
        for key, inner in value.items():
            name = _json_key(key)
            if name in out:                       # 1 and "1" both present
                name = f"\0{type(key).__name__}:{name}"
            out[name] = _canonical(inner)
        return out
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    return value


def _skill_md_bytes(raw: bytes) -> bytes:
    """SKILL.md without the on/off flag, in a stable form."""
    try:
        text = raw.decode("utf-8")
        split = split_frontmatter(text)
        if split is None:
            return raw
        meta = yaml.safe_load(split[0]) or {}
        if not isinstance(meta, dict):
            return raw
        meta.pop("enabled", None)
        metadata = meta.get("metadata")
        if isinstance(metadata, dict):
            harbor = metadata.get("harbor")
            if isinstance(harbor, dict):
                harbor.pop("enabled", None)
                if not harbor:
                    metadata.pop("harbor")      # Settings adds an empty one to hold the flag
            if not metadata:
                meta.pop("metadata")
        return (json.dumps(_canonical(meta), sort_keys=True, default=str) + "\n"
                + split[1].strip()).encode("utf-8")
    except Exception:  # noqa: BLE001 — unparseable: judge the raw bytes
        return raw


_LINKED_TREE_FILES = 2000
# A larger SKILL.md is refused at load (skills._parse) and hashed whole here.
_SKILL_MD_LIMIT = 1 << 20


def _file_digest(path: Path) -> bytes:
    """A regular file's hash, read in chunks; anything else (a FIFO, a device) by its
    type only, so a planted pipe can never block loading."""
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode):
        return hashlib.sha256(b"type:%o" % stat.S_IFMT(info.st_mode)).digest()
    # Non-blocking and checked again on the open file: a FIFO swapped in after the
    # lstat neither blocks the open nor gets read.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return hashlib.sha256(b"type:swapped").digest()
        digest = hashlib.sha256()
        while chunk := os.read(fd, 1 << 20):
            digest.update(chunk)
        return digest.digest()
    finally:
        os.close(fd)


def _read_small(path: Path, limit: int = 1 << 20) -> bytes:
    """A regular file's bytes, at most `limit`, never blocking on a swapped-in FIFO."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return b"type:swapped"
        return os.read(fd, limit)
    finally:
        os.close(fd)


class UnsafeFile(OSError):
    """A file that is not a plain regular file of an acceptable size."""


def read_regular(path: Path, limit: int) -> bytes:
    """The whole of a regular file, refusing a link, a FIFO, a device or a file over
    `limit` bytes. Opened without following a final link and without blocking, and
    checked on the open descriptor, so a swap after any earlier check is caught."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise UnsafeFile(f"{Path(path).name} is a link, not a file") from exc
        raise
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise UnsafeFile(f"{Path(path).name} is not a regular file")
        if info.st_size > limit:
            raise UnsafeFile(f"{Path(path).name} is too large ({info.st_size // 1024} KB; "
                             f"the limit is {limit // 1024} KB)")
        chunks, total = [], 0
        while chunk := os.read(fd, 1 << 16):
            total += len(chunk)
            if total > limit:                      # grew after the fstat
                raise UnsafeFile(f"{Path(path).name} is too large (the limit is {limit // 1024} KB)")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _tree_digest(folder: Path) -> str:
    """A hash of a folder a skill links to: every entry's name, type and contents,
    links inside it hashed by their target, not followed. A tree with more than
    _LINKED_TREE_FILES entries, or one that can't be read in full, never matches an
    approval, so such a skill always loads switched off."""
    digest = hashlib.sha256()
    count = 0
    errors: list[OSError] = []
    for dirpath, dirnames, names in os.walk(folder, followlinks=False, onerror=errors.append):
        dirnames.sort()
        for name in sorted([*names, *(d for d in dirnames if (Path(dirpath) / d).is_symlink())]):
            entry = Path(dirpath) / name
            count += 1
            if count > _LINKED_TREE_FILES:
                return "untrusted-too-large:" + secrets.token_hex(16)
            rel = entry.relative_to(folder).as_posix().encode("utf-8", "replace")
            try:
                if entry.is_symlink():
                    data = hashlib.sha256(b"symlink:" + os.readlink(entry).encode("utf-8", "replace")).digest()
                else:
                    data = _file_digest(entry)
            except OSError:
                return "untrusted-unreadable:" + secrets.token_hex(16)
            digest.update(rel + b"\0" + data)
    if errors:
        return "untrusted-unreadable:" + secrets.token_hex(16)
    return digest.hexdigest()


def fingerprint(skill_dir: Path) -> str:
    """A hash of every file in the skill's folder (names and contents)."""
    digest = hashlib.sha256()
    root = Path(skill_dir)
    files = []
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        # Everything counts, dotfiles and __pycache__ included: a SKILL.md
        # command can run a dotfile, and Python runs a matching .pyc. Only
        # Finder's own .DS_Store is left out.
        if rel.name == ".DS_Store":
            continue
        if path.is_symlink():
            target = os.readlink(path).encode("utf-8", "replace")
            try:
                # What the link points at, too: the content behind it can change.
                if path.is_dir():
                    inner = _tree_digest(path).encode("ascii")
                else:
                    inner = _file_digest(path.resolve()) if path.is_file() else b""
            except OSError:
                inner = b"unreadable"
            files.append((rel.as_posix(), b"symlink:" + target + b"\0" + hashlib.sha256(inner).digest()))
        elif path.is_file():
            if rel.as_posix() == "SKILL.md" and path.stat().st_size <= _SKILL_MD_LIMIT:
                # Small and parsed for the on/off flag; read through the same guard.
                files.append((rel.as_posix(), _skill_md_bytes(_read_small(path, _SKILL_MD_LIMIT))))
            else:
                files.append((rel.as_posix(), b"sha256:" + _file_digest(path)))
    for name, data in sorted(files):
        digest.update(name.encode("utf-8") + b"\0" + hashlib.sha256(data).digest())
    return digest.hexdigest()


def _read() -> dict[str, str]:
    try:
        data = json.loads(TRUST_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)} \
        if isinstance(data, dict) else {}


def _write(data: dict[str, str]) -> None:
    TRUST_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=TRUST_PATH.parent, prefix=".skill_trust.", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=1, sort_keys=True)
        os.replace(tmp, TRUST_PATH)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def approve(skill_dir: Path) -> None:
    """Record the skill folder as the person approved it, as it is now."""
    key = Path(skill_dir).name
    # Fingerprinted under the lock too: two approvals of one folder at once must
    # record the folder as one of them saw it, never an older reading saved last.
    with _LOCK:
        print_ = fingerprint(skill_dir)
        data = _read()
        data[key] = print_
        _write(data)


def forget(skill_dir: Path) -> None:
    key = Path(skill_dir).name
    with _LOCK:
        data = _read()
        if data.pop(key, None) is not None:
            _write(data)


def problem(skill_dir: Path) -> str:
    """Why this user skill must load switched off, or "" when it's approved as it is."""
    approved = _read().get(Path(skill_dir).name)
    if approved is None:
        return NOT_APPROVED
    try:
        current = fingerprint(skill_dir)
    except OSError:
        return CHANGED
    return "" if current == approved else CHANGED
