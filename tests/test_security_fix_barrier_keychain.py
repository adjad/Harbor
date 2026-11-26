"""Auditor note M2: no Keychain call runs while the process-wide publication
barrier (publication_authority._barrier) is held. A locked Keychain can take 30 s
to answer; if a worker thread held the barrier across that call, the event loop
would wait on it the next time it took `revocation()`. Here a stand-in Keychain
blocks every call until released, and the barrier must stay free throughout.
Every Keychain is a stand-in; tokens and addresses are invented.
"""
from __future__ import annotations

import threading
import time

from service import gmail_source as G
from service.config import google_tokens as T
from service.publication_authority import revocation
from tests.test_google_tokens_keychain import ANN, TOKEN_A, FakeKeychain, keychain  # noqa: F401


class BlockingKeychain(FakeKeychain):
    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def __call__(self, argv, input=None, **kwargs):
        if not self.release.is_set():
            self.entered.set()
            self.release.wait(10)
        return super().__call__(argv, input=input, **kwargs)


def _marked() -> dict:
    return {"client_id": "fixture-client", "client_secret": "fixture-secret",
            "accounts": {ANN: {"scopes": [G.SCOPE], G.TOKEN_STORE_KEY: G.KEYCHAIN,
                               G.TOKEN_FINGERPRINT_KEY: T.fingerprint(TOKEN_A)}}}


def _install(monkeypatch, keychain) -> BlockingKeychain:
    slow = BlockingKeychain()
    slow.items = keychain.items
    monkeypatch.setattr(T, "LAUNCH", slow)
    return slow


def _barrier_free_while_keychain_blocked(slow, work) -> None:
    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    try:
        assert slow.entered.wait(5), "the Keychain stand-in was never called"
        started = time.monotonic()
        got = []

        def take():
            with revocation():
                got.append(True)
        taker = threading.Thread(target=take, daemon=True)
        taker.start()
        taker.join(1.0)
        assert got, "the barrier was held across a Keychain call"
        assert time.monotonic() - started < 1.0
    finally:
        slow.release.set()
        thread.join(10)


def test_a_cold_load_under_revocation_reads_keychain_outside_the_barrier(monkeypatch, keychain):
    keychain.items[(T.SERVICE, T.account_name(ANN))] = TOKEN_A
    G._write_file(_marked())
    slow = _install(monkeypatch, keychain)

    def work():
        G.adopt_workspace_updates({ANN: {"scopes": [G.SCOPE, G.CALENDAR_SCOPE]}})
    _barrier_free_while_keychain_blocked(slow, work)


def test_a_save_inside_revocation_moves_its_token_outside_the_barrier(monkeypatch, keychain):
    slow = _install(monkeypatch, keychain)
    done = threading.Event()

    def work():
        with revocation():
            G._save_config({"client_id": "fixture-client", "client_secret": "fixture-secret",
                            "accounts": {ANN: {"refresh_token": TOKEN_A, "scopes": [G.SCOPE]}}})
        done.set()
    _barrier_free_while_keychain_blocked(slow, work)
    assert done.wait(5)
    deadline = time.monotonic() + 5
    while keychain.token(ANN) != TOKEN_A and time.monotonic() < deadline:
        time.sleep(0.02)
    assert keychain.token(ANN) == TOKEN_A           # still moved, by a worker
    deadline = time.monotonic() + 5
    while "refresh_token" in G._read_file()["accounts"][ANN] and time.monotonic() < deadline:
        time.sleep(0.02)
    assert "refresh_token" not in G._read_file()["accounts"][ANN]


def test_a_plain_save_still_moves_its_token_before_returning(keychain):
    G._save_config({"client_id": "fixture-client", "client_secret": "fixture-secret",
                    "accounts": {ANN: {"refresh_token": TOKEN_A, "scopes": [G.SCOPE]}}})
    assert keychain.token(ANN) == TOKEN_A
    assert G._read_file()["accounts"][ANN][G.TOKEN_STORE_KEY] == G.KEYCHAIN


def test_a_load_inside_revocation_after_a_warm_read_has_the_token(keychain):
    keychain.items[(T.SERVICE, T.account_name(ANN))] = TOKEN_A
    G._write_file(_marked())
    G.load_config()                                   # warm, outside the barrier
    with revocation():
        assert G.load_config()["accounts"][ANN]["refresh_token"] == TOKEN_A
