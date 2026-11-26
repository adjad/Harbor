"""Known issue #3: a slow `security` call (a locked Keychain asking for the login
password can take 30 s) doesn't hold Harbor's locks. While one runs on a worker
thread, the token cache and gmail.json saves answer at once. Every Keychain here
is a stand-in; tokens and addresses are invented.
"""
from __future__ import annotations

import threading
import time

from service import gmail_source as G
from service.config import google_tokens as T
from tests.test_google_tokens_keychain import ANN, BEN, TOKEN_A, FakeKeychain, keychain  # noqa: F401


class SlowKeychain(FakeKeychain):
    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def __call__(self, argv, input=None, **kwargs):
        if argv[1:2] == ["find-generic-password"] and not self.release.is_set():
            self.entered.set()
            self.release.wait(10)
        return super().__call__(argv, input=input, **kwargs)


def test_a_slow_keychain_read_holds_neither_lock(monkeypatch, tmp_path, keychain):
    slow = SlowKeychain()
    slow.items = keychain.items
    monkeypatch.setattr(T, "LAUNCH", slow)
    reader = threading.Thread(target=lambda: T.read(ANN), daemon=True)
    reader.start()
    assert slow.entered.wait(5)
    try:
        started = time.monotonic()
        assert T.cached(BEN) is None and T.verified(BEN) is False and T.needs_adopt() is False
        G._save_config({"accounts": {}})            # takes gmail.json's lock, and the cache's
        assert time.monotonic() - started < 1.0
    finally:
        slow.release.set()
        reader.join(5)


def test_reads_and_writes_of_one_item_still_happen_one_at_a_time(monkeypatch, keychain):
    active, most = [0], [0]
    lock = threading.Lock()
    base = keychain

    def counting(argv, input=None, **kwargs):
        with lock:
            active[0] += 1
            most[0] = max(most[0], active[0])
        time.sleep(0.05)
        try:
            return base(argv, input=input, **kwargs)
        finally:
            with lock:
                active[0] -= 1

    monkeypatch.setattr(T, "LAUNCH", counting)
    threads = [threading.Thread(target=T.store, args=(ANN, TOKEN_A)) for _ in range(3)]
    threads += [threading.Thread(target=T.read, args=(ANN,)) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert most[0] == 1 and keychain.token(ANN) == TOKEN_A
