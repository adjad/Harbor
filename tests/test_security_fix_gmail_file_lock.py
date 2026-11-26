"""Known issue #2: Harbor and Quayside share gmail.json. A save in one app waits for
the other's read-modify-write (an advisory lock on gmail.json.lock), so it never
writes from a copy the other has just replaced. And a load on the event loop
starts no `security` process there: the first read of an account happens on a
worker thread. Every Keychain is a stand-in; tokens and addresses are invented.
"""
from __future__ import annotations

import asyncio
import fcntl
import json
import os
import threading
import time

from service import gmail_source as G
from service.config import google_tokens as T
from tests.test_google_tokens_keychain import ANN, BEN, TOKEN_A, keychain  # noqa: F401


def _marked(*emails) -> dict:
    return {"client_id": "fixture-client", "client_secret": "fixture-secret",
            "accounts": {email: {"scopes": [G.SCOPE], G.TOKEN_STORE_KEY: G.KEYCHAIN,
                                 G.TOKEN_FINGERPRINT_KEY: T.fingerprint(TOKEN_A)} for email in emails}}


def _lock_path():
    return G.CONFIG_PATH.with_name(G.CONFIG_PATH.name + ".lock")


def test_a_save_waits_for_the_other_apps_rewrite_and_keeps_what_it_added(keychain):
    G._write_file({"client_id": "fixture-client", "client_secret": "fixture-secret", "accounts": {}})
    held, done = threading.Event(), threading.Event()

    def other_app():
        fd = os.open(_lock_path(), os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        held.set()
        time.sleep(0.3)
        # The other app signs Ben in (his token goes to its Keychain item).
        G.CONFIG_PATH.write_text(json.dumps(_marked(BEN)), encoding="utf-8")
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        done.set()

    worker = threading.Thread(target=other_app)
    worker.start()
    assert held.wait(5)
    started = time.monotonic()
    G._save_config({"client_id": "fixture-client", "client_secret": "fixture-secret", "accounts": {}})
    waited = time.monotonic() - started
    worker.join(5)
    assert done.is_set() and waited >= 0.2
    accounts = json.loads(G.CONFIG_PATH.read_text(encoding="utf-8"))["accounts"]
    assert BEN in accounts and accounts[BEN][G.TOKEN_STORE_KEY] == G.KEYCHAIN


def test_the_lock_file_is_private_and_never_followed(keychain, tmp_path):
    G._save_config({"accounts": {}})
    assert _lock_path().stat().st_mode & 0o777 == 0o600
    _lock_path().unlink()
    target = tmp_path / "elsewhere"
    _lock_path().symlink_to(target)
    G._save_config({"accounts": {}})                 # goes on without the lock
    assert not target.exists() and _lock_path().is_symlink()


def test_a_lock_the_other_app_never_releases_does_not_stop_the_save(keychain, monkeypatch):
    monkeypatch.setattr(G, "FILE_LOCK_WAIT_S", 0.2)
    G._save_config({"accounts": {}})
    fd = os.open(_lock_path(), os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        started = time.monotonic()
        G._save_config({"client_id": "fixture-later", "accounts": {}})
        assert time.monotonic() - started < 2.0
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    assert json.loads(G.CONFIG_PATH.read_text(encoding="utf-8"))["client_id"] == "fixture-later"


def test_a_load_on_the_event_loop_runs_no_keychain_process_there(keychain, monkeypatch):
    keychain.items[(T.SERVICE, T.account_name(ANN))] = TOKEN_A
    G._write_file(_marked(ANN))
    threads, release = [], threading.Event()
    real = T.LAUNCH

    def watching(argv, input=None, **kwargs):
        threads.append(threading.current_thread() is threading.main_thread())
        release.wait(5)                                     # a Keychain that is slow to answer
        return real(argv, input=input, **kwargs)
    monkeypatch.setattr(T, "LAUNCH", watching)

    async def on_loop():
        return G.load_config()
    started = time.monotonic()
    first = asyncio.run(on_loop())
    assert time.monotonic() - started < 1.0
    assert "refresh_token" not in first["accounts"][ANN]   # not read yet: tried again later
    release.set()
    deadline = time.monotonic() + 5
    while T.cached(ANN) is None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert threads and not any(threads)                     # every read ran on a worker
    later = asyncio.run(on_loop())
    assert later["accounts"][ANN]["refresh_token"] == TOKEN_A
