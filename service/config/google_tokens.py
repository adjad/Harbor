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

Harbor and Quayside (the renamed build) share ~/.moe/gmail.json but not a Keychain
service: each saves under its own SERVICE. When an account's item is not under
this app's name, `read` looks under the other app's (OTHER_SERVICES) and, if it
is there, saves a copy under this app's name. The other app's item is never
changed or deleted.

Both apps' items can exist at once (each app signed in, or copied, once), and
a re-sign-in in one app updates only that app's item. So gmail.json also keeps
each account's `fingerprint` of its current token (a salted SHA-256, never the
token): `read` returns only an item whose token matches it, from either name,
and drops a cached token that no longer matches. A stale item is never used.

Nothing here runs `security` on the event loop: a read there that misses the
cache answers None and starts the read on a worker thread, so a later call
finds it cached. Writes, deletes and copies run on a worker thread
(gmail_source.keychain_sync), never under gmail.json's lock, and no lock here
is held while a `security` process runs except the one that keeps them one at
a time.

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
# Both apps' names, each assembled in pieces so the Harbor -> Quayside import leaves them
# alone: it rewrites every spelled-out "harbor" to "quayside", and first every spelled-out
# "quayside" to "quayside" (fixture names), so a literal of either would come out wrong.
# In the Quayside build SERVICE becomes the Quayside name and this tuple still names both.
# (Only keychain_legacy.py may carry the keychain-legacy-name marker.)
_BOTH = ("com." + "wi" + "sp" + ".google", "com." + "har" + "bor" + ".google")
OTHER_SERVICES = tuple(name for name in _BOTH if name != SERVICE)
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
# Held across each `security` process, so calls on one item still happen one at a
# time; _lock guards only the in-memory state below and is never held while a
# process runs. A locked Keychain can keep `security` waiting up to TIMEOUT_S for
# the login password, and the cache checks gmail_source makes under its own lock
# (cached, verified, needs_adopt) must not wait that long.
_io_lock = threading.Lock()
_cache: dict[str, str] = {}           # address -> token confirmed in Keychain this run
_adopt: set[str] = set()              # found only under the other app's name; copy it to ours
# Cached tokens known to be current: written by this run, or read against the
# file's fingerprint. A token read from a file with no fingerprint yet (an
# older Keychain build) may be stale and is never fingerprinted or copied over
# an existing item.
_verified: set[str] = set()
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


def on_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def _timeout() -> float:
    return LOOP_TIMEOUT_S if on_loop() else TIMEOUT_S


def _run(argv: list[str], *, stdin: bytes | None = None):
    try:
        return LAUNCH(["/usr/bin/security", *argv], input=stdin, stdout=subprocess.PIPE,
                      stderr=subprocess.PIPE, timeout=_timeout(), env=_ENV, check=False)
    except (OSError, subprocess.SubprocessError, ValueError):
        raise Unavailable from None


def _read(email: str, service: str = SERVICE) -> str | None:
    """The token in Keychain, None when there is no item. Raises Unavailable."""
    result = _run(["find-generic-password", "-s", service, "-a", account_name(email), "-w"])
    if result.returncode == NOT_FOUND:
        return None
    try:
        value = (result.stdout or b"").decode("ascii").rstrip("\n")
    except UnicodeDecodeError:
        raise Unavailable from None
    if result.returncode or not _SAFE.fullmatch(value):
        raise Unavailable
    return value


# No product name in the salt: both apps must compute the same fingerprint.
_FINGERPRINT_SALT = b"google-refresh-token-v1:"


def fingerprint(token: str) -> str:
    """What gmail.json records about the current token: enough to tell which
    Keychain item is current, nothing that could stand in for the token."""
    return hashlib.sha256(_FINGERPRINT_SALT + token.encode("utf-8")).hexdigest()


def _current(token: str | None, want: str | None) -> bool:
    return bool(token) and (want is None or fingerprint(token) == want)


def verified(email: str) -> bool:
    with _lock:
        return email.strip().lower() in _verified


def cached(email: str, want: str | None = None) -> str | None:
    """The token confirmed this run, if it is still the current one (`want`)."""
    with _lock:
        token = _cache.get(email.strip().lower())
        return token if _current(token, want) else None


def read(email: str, want: str | None = None) -> str | None:
    """The account's current token from Keychain: this app's item, else the
    other app's, whichever matches the fingerprint `want` (any token when the
    file has none yet, as written by the first Keychain build). None when no
    item matches or Keychain can't be read right now. Cached after the first
    read; a failed read is not retried for READ_HOLD_S, so status polls don't
    keep asking. On the event loop it runs no process and waits for no lock:
    a cache miss answers None and reads on a worker thread (_read_soon)."""
    email = email.strip().lower()
    if on_loop():
        if not _lock.acquire(blocking=False):
            return None
        try:
            if _current(_cache.get(email), want):
                return _cache[email]
        finally:
            _lock.release()
        _read_soon(email, want)
        return None
    with _lock:
        if _current(_cache.get(email), want):
            return _cache[email]
    _io_lock.acquire()
    try:
        with _lock:
            if _current(_cache.get(email), want):
                return _cache[email]       # read by the call this one waited for
            _cache.pop(email, None)
            _verified.discard(email)
            failed = _read_failed.get(email)
            if failed is not None and time.monotonic() - failed < READ_HOLD_S:
                return None
        found, adopt = None, False
        try:
            mine = _read(email)
            if _current(mine, want):
                found = mine
            else:
                for other in OTHER_SERVICES:
                    theirs = _read(email, other)
                    if _current(theirs, want):
                        found, adopt = theirs, True
                        break
        except Unavailable:
            with _lock:
                _read_failed[email] = time.monotonic()
            return None
        with _lock:
            _read_failed.pop(email, None)
            if adopt:
                _adopt.add(email)
            if found is not None:
                _cache[email] = found
                if want is None:
                    _verified.discard(email)
                else:
                    _verified.add(email)
        return found
    finally:
        _io_lock.release()


_reading: set[tuple[str, str | None]] = set()


def _read_soon(email: str, want: str | None) -> None:
    """Read one account on a worker thread (once at a time per account), for a
    read the event loop could not make itself."""
    key = (email, want)
    with _lock:
        if key in _reading:
            return
        _reading.add(key)

    def work():
        try:
            read(email, want)
        except Exception:  # noqa: BLE001 — a failed read is held like any other
            pass
        finally:
            with _lock:
                _reading.discard(key)
    threading.Thread(target=work, name="google-token-read", daemon=True).start()


def adopt_pending() -> None:
    """Copy every token found only under the other app's name to this app's
    name. Worker thread only (gmail_source.keychain_sync)."""
    with _lock:
        pending = [(email, _cache.get(email)) for email in sorted(_adopt)]
    for email, token in pending:
        if token and _store(email, token):
            with _lock:
                _adopt.discard(email)


def needs_adopt() -> bool:
    with _lock:
        return bool(_adopt)


def store(email: str, token: str) -> bool:
    """Save the token and confirm it reads back exactly. True only then; on
    False the caller must keep its own copy (the file). Never on the event
    loop: it answers False there and the caller's file copy stays."""
    if on_loop():
        return False
    return _store(email, token)


def _store(email: str, token: str) -> bool:
    global _move_failed_at
    email = email.strip().lower()
    if not token or not _SAFE.fullmatch(token):
        return False
    with _io_lock:
        with _lock:
            if _cache.get(email) == token and email not in _adopt and email in _verified:
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
        with _lock:
            if not confirmed:
                if email not in _adopt:
                    _cache.pop(email, None)
                _move_failed_at = time.monotonic()
                return False
            _cache[email] = token
            _verified.add(email)
            _adopt.discard(email)
            _read_failed.pop(email, None)
            _move_failed_at = None
            return True


def delete(email: str) -> bool:
    """Remove this app's item for the account (never the other app's). True
    when it is gone (or was never there). Never on the event loop."""
    if on_loop():
        return False
    email = email.strip().lower()
    with _io_lock:
        with _lock:
            _cache.pop(email, None)
            _verified.discard(email)
            _adopt.discard(email)
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
        _verified.clear()
        _adopt.clear()
        _read_failed.clear()
        _move_failed_at = None
