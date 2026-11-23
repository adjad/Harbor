"""Google sign-in refresh tokens in the login Keychain, one item per account.

gmail.json (service/gmail_source.py) keeps everything about a connected Google
account except its refresh token, which lives here under SERVICE with the
account name `google:<sha256 of the lowercased address>` (so no address is
put through `security -i` and none is shown in Keychain Access). The file
marks such an account with `refresh_token_store: "keychain"`.

A token is counted as stored only when it reads back from Keychain exactly as
written (`store`). Until then it stays in gmail.json, 0600, as before: that is
the fallback when Keychain access is denied, times out or is locked, and the
next save or launch tries again. So a token is never only in memory: it is in
the file, in Keychain, or (between a confirmed write and the file's rewrite)
in both.

Like service/config/keychain_legacy.py, this starts `/usr/bin/security`: the
token is read from its stdout and written through `security -i`'s stdin, so it
never reaches argv, a log or an error. Items `security` adds trust `security`
itself, so reading them back raises no macOS question. Under tests (and with
HARBOR_KEYCHAIN_LEGACY=off or HARBOR_GOOGLE_KEYCHAIN=off) the real process is
refused and every call behaves as Keychain being unavailable, unless a test
installs its own stand-in as LAUNCH.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
import threading
import time

from . import keychain_legacy

SERVICE = "com.harbor.google"
NOT_FOUND = keychain_legacy.NOT_FOUND
# A token that can pass through `security -i` unquoted. Google refresh tokens
# ("1//0g…", letters, digits, - _ / .) fit; anything else stays in the file.
_SAFE = keychain_legacy._SAFE
_ENV = {"PATH": "/usr/bin:/bin", "LC_ALL": "C"}
# How long a call may wait on `security`. Off the event loop a locked Keychain
# can ask for the login password, which takes a person this long.
TIMEOUT_S = 30.0
LOOP_TIMEOUT_S = 5.0
# After Keychain fails, launch-time moves wait this long before trying again
# (each try may raise a macOS question). Explicit saves always try.
MOVE_HOLD_S = 600.0
# A failed read of one account waits this long before the next read.
READ_HOLD_S = 30.0

_lock = threading.RLock()
_cache: dict[str, str] = {}           # address -> token confirmed in Keychain this run
_read_failed: dict[str, float] = {}   # address -> monotonic time of its last failed read
_move_failed_at: float | None = None


class Unavailable(Exception):
    """Keychain did not answer (refused, denied, timed out or malformed)."""


class RealKeychainRefused(PermissionError):
    """A real `security` launch was refused (under tests, or switched off)."""


def _real_launch(argv: list[str], **kwargs):
    if (os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("HARBOR_KEYCHAIN_LEGACY") == "off"
            or os.environ.get("HARBOR_GOOGLE_KEYCHAIN") == "off"):
        raise RealKeychainRefused("google_tokens: a real security process is refused here")
    return subprocess.run(argv, **kwargs)


# The one place this module starts a process. Tests install a stand-in Keychain here.
LAUNCH = _real_launch


def account_name(email: str) -> str:
    return "google:" + hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def _timeout() -> float:
    try:
        asyncio.get_running_loop()
        return LOOP_TIMEOUT_S
    except RuntimeError:
        return TIMEOUT_S


def _run(argv: list[str], *, stdin: bytes | None = None):
    try:
        return LAUNCH(["/usr/bin/security", *argv], input=stdin, stdout=subprocess.PIPE,
                      stderr=subprocess.PIPE, timeout=_timeout(), env=_ENV, check=False)
    except (OSError, subprocess.SubprocessError, ValueError):
        raise Unavailable from None


def _read(email: str) -> str | None:
    """The token in Keychain, None when there is no item. Raises Unavailable."""
    result = _run(["find-generic-password", "-s", SERVICE, "-a", account_name(email), "-w"])
    if result.returncode == NOT_FOUND:
        return None
    try:
        value = (result.stdout or b"").decode("ascii").rstrip("\n")
    except UnicodeDecodeError:
        raise Unavailable from None
    if result.returncode or not _SAFE.fullmatch(value):
        raise Unavailable
    return value


def cached(email: str) -> str | None:
    with _lock:
        return _cache.get(email.strip().lower())


def read(email: str) -> str | None:
    """The account's token from Keychain (cached after the first read), or None
    when Keychain has none or cannot be read right now. A failed read is not
    retried for READ_HOLD_S, so status polls don't keep asking."""
    email = email.strip().lower()
    with _lock:
        if email in _cache:
            return _cache[email]
        failed = _read_failed.get(email)
        if failed is not None and time.monotonic() - failed < READ_HOLD_S:
            return None
        try:
            value = _read(email)
        except Unavailable:
            _read_failed[email] = time.monotonic()
            return None
        _read_failed.pop(email, None)
        if value is not None:
            _cache[email] = value
        return value


def store(email: str, token: str) -> bool:
    """Save the token and confirm it reads back exactly. True only then; on
    False the caller must keep its own copy (the file)."""
    global _move_failed_at
    email = email.strip().lower()
    if not token or not _SAFE.fullmatch(token):
        return False
    with _lock:
        if _cache.get(email) == token:
            return True
        command = f"add-generic-password -U -s {SERVICE} -a {account_name(email)} -w {token}\n"
        try:
            # `security -i` reads its command from stdin: the token never reaches argv.
            _run(["-i"], stdin=command.encode("ascii"))
            confirmed = _read(email) == token
        except Unavailable:
            confirmed = False
        finally:
            del command
        if not confirmed:
            _cache.pop(email, None)
            _move_failed_at = time.monotonic()
            return False
        _cache[email] = token
        _read_failed.pop(email, None)
        _move_failed_at = None
        return True


def delete(email: str) -> bool:
    """Remove the account's item. True when it is gone (or was never there)."""
    email = email.strip().lower()
    with _lock:
        _cache.pop(email, None)
        _read_failed.pop(email, None)
        try:
            result = _run(["delete-generic-password", "-s", SERVICE, "-a", account_name(email)])
        except Unavailable:
            return False
        return result.returncode in (0, NOT_FOUND)


def may_move() -> bool:
    """Whether a launch-time move from the file should try Keychain now."""
    with _lock:
        return _move_failed_at is None or time.monotonic() - _move_failed_at >= MOVE_HOLD_S


def read_failed(email: str) -> bool:
    """The last read of this account's item failed (Keychain unavailable)."""
    with _lock:
        return email.strip().lower() in _read_failed


def forget_cache() -> None:
    """Drop everything held for this run (tests, and a reset)."""
    global _move_failed_at
    with _lock:
        _cache.clear()
        _read_failed.clear()
        _move_failed_at = None
