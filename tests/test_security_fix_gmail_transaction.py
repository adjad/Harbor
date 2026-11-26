"""Auditor note M3: gmail.json's advisory lock (gmail.json.lock, shared by Harbor and
Quayside) covers a whole load -> merge -> save, not just the write. If it covered
only the write, the other app could sign an account in between this app's load
and its save, and the save, built from the older load, would drop it.

The "other app" here is a thread doing its own read-modify-write under the lock;
it signs Ben in, with his token still in the file (its Keychain didn't confirm
it), exactly while this app is between its load and its save. Every Keychain is a
stand-in; tokens and addresses are invented.
"""
from __future__ import annotations

import fcntl
import json
import os
import threading

from service import gmail_source as G
from service.config import google_tokens as T
from tests.test_google_tokens_keychain import ANN, BEN, TOKEN_A, TOKEN_B, keychain  # noqa: F401


def _lock_path():
    return G.CONFIG_PATH.with_name(G.CONFIG_PATH.name + ".lock")


def _other_app_signs_ben_in(done: threading.Event) -> None:
    fd = os.open(_lock_path(), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        data = json.loads(G.CONFIG_PATH.read_text(encoding="utf-8"))
        data.setdefault("accounts", {})[BEN] = {"refresh_token": TOKEN_B, "scopes": [G.SCOPE]}
        G.CONFIG_PATH.write_text(json.dumps(data), encoding="utf-8")
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
        done.set()


def _between_load_and_save(monkeypatch):
    """Start the other app's sign-in right after this app's load, and give it a
    moment to land (it can't while this app holds the lock)."""
    real = G.connected_accounts
    started = []

    def hooked(cfg=None):
        result = real(cfg)
        if cfg is not None and not started:
            done = threading.Event()
            started.append((threading.Thread(target=_other_app_signs_ben_in, args=(done,)), done))
            started[0][0].start()
            done.wait(0.4)
        return result
    monkeypatch.setattr(G, "connected_accounts", hooked)
    return started


def _start(keychain):
    keychain.items[(T.SERVICE, T.account_name(ANN))] = TOKEN_A
    G._write_file({"client_id": "fixture-client", "client_secret": "fixture-secret",
                   "accounts": {ANN: {"scopes": [G.SCOPE], "workspace": False,
                                      G.TOKEN_STORE_KEY: G.KEYCHAIN,
                                      G.TOKEN_FINGERPRINT_KEY: T.fingerprint(TOKEN_A)}}})


def _finish(started):
    thread, done = started[0]
    thread.join(5)
    assert done.is_set()
    return json.loads(G.CONFIG_PATH.read_text(encoding="utf-8"))["accounts"]


def test_a_sign_in_from_the_other_app_between_load_and_save_is_kept(monkeypatch, keychain):
    _start(keychain)
    started = _between_load_and_save(monkeypatch)
    G.adopt_workspace_updates({ANN: {"scopes": [G.SCOPE, G.CALENDAR_SCOPE]}})
    accounts = _finish(started)
    assert BEN in accounts, "the other app's sign-in was dropped"
    assert G.CALENDAR_SCOPE in accounts[ANN]["scopes"]


def test_switching_workspace_keeps_the_other_apps_sign_in(monkeypatch, keychain):
    _start(keychain)
    started = _between_load_and_save(monkeypatch)
    monkeypatch.setattr("service.google_workspace.sync_credentials", lambda *a, **k: None)
    G.set_workspace(ANN, True)
    accounts = _finish(started)
    assert BEN in accounts and accounts[ANN]["workspace"] is True


def test_the_lock_is_taken_once_per_transaction(keychain):
    _start(keychain)
    G.load_config()
    with G._config_transaction():
        cfg = G.load_config()
        G._save_config(cfg)                   # nested: no second wait on our own lock
        assert G._keychain_barred()           # and no Keychain call under it
