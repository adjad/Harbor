"""Google refresh tokens move from gmail.json to the login Keychain, and are never lost.

Every Keychain here is a stand-in (FakeKeychain, installed as google_tokens.LAUNCH):
no real `security` process starts, and every token and address is invented.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

_scratch = tempfile.TemporaryDirectory(prefix="harbor-google-tokens-")
os.environ.setdefault("HARBOR_HOME", _scratch.name)

from service import gmail_source as G  # noqa: E402
from service.config import google_tokens as T  # noqa: E402

ANN = "ann.fixture@example.test"
BEN = "ben.fixture@example.test"
TOKEN_A = "1//0fake-refresh-token-ann_A1"
TOKEN_B = "1//0fake-refresh-token-ben_B2"


class FakeKeychain:
    """A stand-in for `/usr/bin/security`: find/delete by argv, add through `-i` stdin."""

    def __init__(self):
        self.items: dict[tuple[str, str], str] = {}
        self.deny = False          # every call refused (access denied / no answer)
        self.drop_writes = False   # `add` answers but nothing is saved
        self.corrupt_writes = False
        self.fail_deletes = False
        self.calls: list[list[str]] = []

    def __call__(self, argv, input=None, **kwargs):
        assert argv[0] == "/usr/bin/security"
        args = argv[1:]
        self.calls.append(args)
        # A token never travels in argv.
        for value in (TOKEN_A, TOKEN_B):
            assert all(value not in part for part in argv)
        if self.deny:
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout", 0))
        if args == ["-i"]:
            words = input.decode("ascii").split()
            assert words[:2] == ["add-generic-password", "-U"]
            service, account, value = words[words.index("-s") + 1], words[words.index("-a") + 1], words[words.index("-w") + 1]
            if not self.drop_writes:
                self.items[(service, account)] = value + ("x" if self.corrupt_writes else "")
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        key = (args[args.index("-s") + 1], args[args.index("-a") + 1])
        if args[0] == "find-generic-password":
            if key not in self.items:
                return subprocess.CompletedProcess(argv, T.NOT_FOUND, b"", b"")
            return subprocess.CompletedProcess(argv, 0, (self.items[key] + "\n").encode(), b"")
        if args[0] == "delete-generic-password":
            if self.fail_deletes:
                return subprocess.CompletedProcess(argv, 1, b"", b"")
            return subprocess.CompletedProcess(argv, 0 if self.items.pop(key, None) else T.NOT_FOUND, b"", b"")
        raise AssertionError(f"unexpected security call: {args}")

    def token(self, email):
        return self.items.get((T.SERVICE, T.account_name(email)))


@pytest.fixture
def keychain(monkeypatch, tmp_path):
    fake = FakeKeychain()
    monkeypatch.setattr(T, "LAUNCH", fake)
    monkeypatch.setattr(G, "CONFIG_PATH", tmp_path / "gmail.json")
    monkeypatch.setattr(G, "MOE_DIR", tmp_path)
    T.forget_cache()
    yield fake
    T.forget_cache()


def write_file(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def file_data() -> dict:
    return json.loads(G.CONFIG_PATH.read_text(encoding="utf-8"))


def old_file(*emails_tokens) -> dict:
    return {"client_id": "fixture-client", "client_secret": "fixture-secret",
            "accounts": {email: {"refresh_token": token, "scopes": [G.SCOPE], "workspace": True}
                         for email, token in emails_tokens}}


def test_the_real_security_process_is_refused_under_tests(monkeypatch):
    monkeypatch.setattr(T, "LAUNCH", T._real_launch)
    T.forget_cache()
    with pytest.raises(T.RealKeychainRefused):
        T._real_launch(["/usr/bin/security", "list-keychains"])
    assert T.store(ANN, TOKEN_A) is False and T.read(ANN) is None
    T.forget_cache()


def test_an_old_file_is_moved_once_and_the_token_leaves_it_only_after_keychain_confirms(keychain):
    write_file(G.CONFIG_PATH, old_file((ANN, TOKEN_A), (BEN, TOKEN_B)))
    cfg = G.load_config()
    assert G.connected_accounts(cfg)[ANN]["refresh_token"] == TOKEN_A
    assert G.connected_accounts(cfg)[BEN]["refresh_token"] == TOKEN_B
    assert keychain.token(ANN) == TOKEN_A and keychain.token(BEN) == TOKEN_B
    raw = file_data()
    for email in (ANN, BEN):
        assert "refresh_token" not in raw["accounts"][email]
        assert raw["accounts"][email][G.TOKEN_STORE_KEY] == G.KEYCHAIN
        assert raw["accounts"][email]["scopes"] == [G.SCOPE]      # everything else stays
    assert TOKEN_A not in G.CONFIG_PATH.read_text() and TOKEN_B not in G.CONFIG_PATH.read_text()
    assert oct(G.CONFIG_PATH.stat().st_mode & 0o777) == "0o600"
    # The marker never leaks out to callers, and a second load doesn't write again.
    assert G.TOKEN_STORE_KEY not in G.connected_accounts(cfg)[ANN]
    writes = sum(call == ["-i"] for call in keychain.calls)
    assert G.connected_accounts(G.load_config())[ANN]["refresh_token"] == TOKEN_A
    assert sum(call == ["-i"] for call in keychain.calls) == writes
    assert G.token_storage() == "keychain"


def test_a_fresh_run_reads_the_token_back_from_keychain(keychain):
    write_file(G.CONFIG_PATH, old_file((ANN, TOKEN_A)))
    G.load_config()
    T.forget_cache()                      # a new launch: nothing held in memory
    assert G.connected_accounts(G.load_config())[ANN]["refresh_token"] == TOKEN_A


@pytest.mark.parametrize("failure", ["deny", "drop_writes", "corrupt_writes"])
def test_a_keychain_that_refuses_or_does_not_keep_the_token_leaves_it_in_the_file(keychain, failure):
    setattr(keychain, failure, True)
    write_file(G.CONFIG_PATH, old_file((ANN, TOKEN_A)))
    cfg = G.load_config()
    assert G.connected_accounts(cfg)[ANN]["refresh_token"] == TOKEN_A
    raw = file_data()
    assert raw["accounts"][ANN]["refresh_token"] == TOKEN_A
    assert G.TOKEN_STORE_KEY not in raw["accounts"][ANN]
    assert G.token_storage() == "file"
    # A save while Keychain still fails keeps the file as the fallback store.
    G._save_config({"client_id": "fixture-client", "client_secret": "fixture-secret",
                    "accounts": G.connected_accounts(cfg)})
    assert file_data()["accounts"][ANN]["refresh_token"] == TOKEN_A
    # Once Keychain works again, the next save moves it.
    setattr(keychain, failure, False)
    keychain.items.clear()
    G._save_config({"client_id": "fixture-client", "client_secret": "fixture-secret",
                    "accounts": G.connected_accounts(G.load_config())})
    assert keychain.token(ANN) == TOKEN_A and "refresh_token" not in file_data()["accounts"][ANN]


def test_a_launch_after_a_failed_move_waits_before_asking_keychain_again(keychain, monkeypatch):
    keychain.deny = True
    write_file(G.CONFIG_PATH, old_file((ANN, TOKEN_A)))
    G.load_config()
    asked = len(keychain.calls)
    G.load_config()
    assert len(keychain.calls) == asked           # held: no new question every status poll
    monkeypatch.setattr(T, "MOVE_HOLD_S", 0.0)
    keychain.deny = False
    G.load_config()
    assert keychain.token(ANN) == TOKEN_A


def test_a_crash_between_the_keychain_write_and_the_file_rewrite_loses_nothing(keychain, monkeypatch):
    write_file(G.CONFIG_PATH, old_file((ANN, TOKEN_A)))

    def crash(data):
        raise OSError("synthetic disk full")
    monkeypatch.setattr(G, "_write_file", crash)
    cfg = G.load_config()
    assert G.connected_accounts(cfg)[ANN]["refresh_token"] == TOKEN_A
    assert file_data()["accounts"][ANN]["refresh_token"] == TOKEN_A   # the file still has it
    assert keychain.token(ANN) == TOKEN_A                              # and Keychain has a copy


def test_the_file_wins_over_an_older_keychain_copy(keychain):
    # A sign-in saved while Keychain was down: the file has the newer token.
    keychain.items[(T.SERVICE, T.account_name(ANN))] = "1//0older-token"
    raw = old_file((ANN, TOKEN_A))
    raw["accounts"][ANN][G.TOKEN_STORE_KEY] = G.KEYCHAIN
    write_file(G.CONFIG_PATH, raw)
    assert G.connected_accounts(G.load_config())[ANN]["refresh_token"] == TOKEN_A
    assert keychain.token(ANN) == TOKEN_A


def test_an_unreadable_keychain_item_never_drops_the_account_from_the_file(keychain):
    write_file(G.CONFIG_PATH, old_file((ANN, TOKEN_A), (BEN, TOKEN_B)))
    G.load_config()
    T.forget_cache()
    # Ben's item can't be read this run (Keychain locked); Ann's is cached.
    T.read(ANN)
    keychain.deny = True
    cfg = G.load_config()
    accounts = G.connected_accounts(cfg)
    assert set(accounts) == {ANN}                     # Ben reads as not connected for now
    # Any save built from that view (an expiry mark, a Workspace switch) keeps Ben.
    accounts[ANN] = {**accounts[ANN], "workspace": False}
    G._save_config({"client_id": cfg.get("client_id", ""), "client_secret": cfg.get("client_secret", ""),
                    "accounts": accounts})
    raw = file_data()
    assert raw["accounts"][BEN][G.TOKEN_STORE_KEY] == G.KEYCHAIN and raw["accounts"][BEN]["scopes"] == [G.SCOPE]
    assert raw["accounts"][ANN]["workspace"] is False
    assert keychain.token(BEN) == TOKEN_B             # nothing deleted
    keychain.deny = False
    T.forget_cache()
    assert G.connected_accounts(G.load_config())[BEN]["refresh_token"] == TOKEN_B


def test_a_new_sign_in_replaces_the_keychain_token(keychain):
    write_file(G.CONFIG_PATH, old_file((ANN, TOKEN_A)))
    cfg = G.load_config()
    accounts = G.connected_accounts(cfg)
    accounts[ANN] = {**accounts[ANN], "refresh_token": "1//0fake-new-sign-in"}
    G._save_config({"client_id": "fixture-client", "client_secret": "fixture-secret", "accounts": accounts})
    assert keychain.token(ANN) == "1//0fake-new-sign-in"
    assert "1//0fake-new-sign-in" not in G.CONFIG_PATH.read_text()
    T.forget_cache()
    assert G.connected_accounts(G.load_config())[ANN]["refresh_token"] == "1//0fake-new-sign-in"


def test_a_token_security_cannot_carry_stays_in_the_file(keychain):
    odd = "token with spaces"
    write_file(G.CONFIG_PATH, old_file((ANN, odd)))
    assert G.connected_accounts(G.load_config())[ANN]["refresh_token"] == odd
    assert file_data()["accounts"][ANN]["refresh_token"] == odd
    assert not any(call == ["-i"] for call in keychain.calls)


def test_the_single_account_form_is_moved_too(keychain):
    write_file(G.CONFIG_PATH, {"client_id": "fixture-client", "client_secret": "fixture-secret",
                               "email": ANN, "refresh_token": TOKEN_A})
    cfg = G.load_config()
    assert G.connected_accounts(cfg)[ANN]["refresh_token"] == TOKEN_A
    raw = file_data()
    assert "refresh_token" not in raw and raw[G.TOKEN_STORE_KEY] == G.KEYCHAIN
    assert keychain.token(ANN) == TOKEN_A


def _disconnect(account=""):
    from unittest.mock import patch
    with patch.object(G, "erase_account_data", lambda email, attachment_text=True: {"mail": "erased"}), \
         patch.object(G, "_erase_attachment_text", lambda email: "erased"), \
         patch("service.tools.email_tools.configure_external_accounts", lambda accounts: None), \
         patch.object(G.httpx, "AsyncClient", side_effect=OSError("offline in tests")):
        return asyncio.run(G.disconnect(account))


def test_disconnecting_one_account_deletes_only_its_keychain_item_after_the_file(keychain):
    write_file(G.CONFIG_PATH, old_file((ANN, TOKEN_A), (BEN, TOKEN_B)))
    G.load_config()
    result = _disconnect(BEN)
    assert result["removed"] == [BEN] and result["revoked"] == {BEN: "offline"}
    assert keychain.token(BEN) is None and keychain.token(ANN) == TOKEN_A
    assert set(file_data()["accounts"]) == {ANN}


def test_disconnecting_everything_removes_every_keychain_item_even_an_unreadable_one(keychain):
    write_file(G.CONFIG_PATH, {"accounts": old_file((ANN, TOKEN_A), (BEN, TOKEN_B))["accounts"]})
    G.load_config()
    T.forget_cache()
    T.read(ANN)
    T._read_failed[BEN] = 10 ** 12           # Ben's read failed moments ago: held
    _disconnect()
    assert not G.CONFIG_PATH.exists()
    assert keychain.token(ANN) is None and keychain.token(BEN) is None


def test_a_failed_keychain_delete_is_reported_and_the_file_still_forgets_the_account(keychain, capsys):
    write_file(G.CONFIG_PATH, old_file((ANN, TOKEN_A), (BEN, TOKEN_B)))
    G.load_config()
    keychain.fail_deletes = True
    _disconnect(BEN)
    assert set(file_data()["accounts"]) == {ANN}
    out = capsys.readouterr().out
    assert "could not remove a disconnected account's Keychain item" in out
    assert TOKEN_B not in out and BEN not in out


def test_status_reports_where_tokens_are_without_the_token(keychain):
    write_file(G.CONFIG_PATH, old_file((ANN, TOKEN_A)))
    G.load_config()
    assert G.token_storage() == "keychain"
    raw = file_data()
    raw["accounts"][BEN] = {"refresh_token": TOKEN_B}
    write_file(G.CONFIG_PATH, raw)
    assert G.token_storage() == "mixed"


def test_reset_still_warns_when_the_only_token_is_in_keychain(tmp_path):
    from service import harbor_reset
    write_file(tmp_path / "gmail.json", {"accounts": {ANN: {G.TOKEN_STORE_KEY: G.KEYCHAIN}}})
    assert harbor_reset._google_still_connected(tmp_path)
    write_file(tmp_path / "gmail.json", {"accounts": {ANN: {"scopes": []}}})
    assert not harbor_reset._google_still_connected(tmp_path)


def _unreadable(email):
    T._read_failed[email] = 10 ** 12          # its read failed moments ago: held


def test_disconnecting_one_account_keeps_another_whose_item_is_unreadable(keychain):
    # Built-in client: no top-level client_id, so an empty account list used to delete the file.
    accounts = old_file((ANN, TOKEN_A), (BEN, TOKEN_B))["accounts"]
    for item in accounts.values():
        item.update(client_id="fixture-builtin", client_source="builtin")
    write_file(G.CONFIG_PATH, {"accounts": accounts})
    G.load_config()
    T.forget_cache()
    T.read(ANN)
    _unreadable(BEN)
    result = _disconnect(ANN)
    assert result["removed"] == [ANN]
    raw = file_data()
    assert set(raw["accounts"]) == {BEN} and raw["accounts"][BEN][G.TOKEN_STORE_KEY] == G.KEYCHAIN
    assert keychain.token(BEN) == TOKEN_B and keychain.token(ANN) is None
    T.forget_cache()
    assert G.connected_accounts(G.load_config())[BEN]["refresh_token"] == TOKEN_B


def test_an_unreadable_account_can_still_be_disconnected_by_name(keychain):
    write_file(G.CONFIG_PATH, old_file((ANN, TOKEN_A), (BEN, TOKEN_B)))
    G.load_config()
    T.forget_cache()
    T.read(ANN)
    _unreadable(BEN)
    result = _disconnect(BEN)
    assert result["removed"] == [BEN] and result["revoked"] == {BEN: "unreadable"}
    assert set(file_data()["accounts"]) == {ANN} and keychain.token(BEN) is None
    assert keychain.token(ANN) == TOKEN_A


def test_a_new_sign_in_reaches_the_file_before_keychain(keychain, monkeypatch):
    # An older token is still in the file (a save while Keychain was down).
    keychain.deny = True
    write_file(G.CONFIG_PATH, old_file((ANN, TOKEN_A)))
    G.load_config()
    keychain.deny = False
    writes = []
    real = G._write_file

    def write(data):
        writes.append(json.loads(json.dumps(data)))
        if len(writes) == 2:
            raise OSError("synthetic crash after the Keychain write")
        real(data)
    monkeypatch.setattr(G, "_write_file", write)
    accounts = {ANN: {"client_id": "fixture-client", "refresh_token": "1//0fake-newest"}}
    G._save_config({"client_id": "fixture-client", "client_secret": "fixture-secret", "accounts": accounts})
    monkeypatch.setattr(G, "_write_file", real)
    # The first write put the newest token on disk; the rewrite that would take it out failed.
    assert writes[0]["accounts"][ANN]["refresh_token"] == "1//0fake-newest" and len(writes) == 2
    # The newest token was on disk before Keychain was touched, so it wins on the next load.
    assert file_data()["accounts"][ANN]["refresh_token"] == "1//0fake-newest"
    T.forget_cache()
    assert G.connected_accounts(G.load_config())[ANN]["refresh_token"] == "1//0fake-newest"
    assert keychain.token(ANN) == "1//0fake-newest"


# ---- Harbor and Quayside share gmail.json, each with its own Keychain name ----

def test_the_renamed_build_still_knows_the_other_apps_name():
    import re
    source = Path(T.__file__).read_text(encoding="utf-8")
    # What the Harbor -> Quayside import does to every spelled-out product name.
    renamed = re.sub(r"harbor", "quayside", re.sub(r"Harbor", "Quayside", source))
    renamed = renamed.replace("from . import keychain_legacy", "from service.config import keychain_legacy")
    space: dict = {"__name__": "renamed_google_tokens"}
    exec(compile(renamed, "renamed_google_tokens.py", "exec"), space)
    assert space["SERVICE"] == "com.quayside.google"
    assert space["OTHER_SERVICES"] == ("com.harbor.google",)
    assert T.SERVICE == "com.harbor.google" and T.OTHER_SERVICES == ("com.quayside.google",)


def test_an_item_saved_by_the_other_app_is_read_and_copied_never_moved(keychain):
    other = T.OTHER_SERVICES[0]
    keychain.items[(other, T.account_name(ANN))] = TOKEN_A
    write_file(G.CONFIG_PATH, {"client_id": "fixture-client",
                               "accounts": {ANN: {G.TOKEN_STORE_KEY: G.KEYCHAIN, "scopes": [G.SCOPE]}}})
    cfg = G.load_config()
    assert G.connected_accounts(cfg)[ANN]["refresh_token"] == TOKEN_A       # not "disconnected"
    assert keychain.token(ANN) == TOKEN_A                                   # copied under our name
    assert keychain.items[(other, T.account_name(ANN))] == TOKEN_A          # theirs untouched
    T.forget_cache()
    G.load_config()
    finds = [c for c in keychain.calls if c[0] == "find-generic-password"]
    assert finds[-1][2] == T.SERVICE                                        # ours is found first now
    # A disconnect deletes only this app's item.
    _disconnect(ANN)
    assert keychain.token(ANN) is None
    assert keychain.items[(other, T.account_name(ANN))] == TOKEN_A
    assert not any(c[0] == "delete-generic-password" and c[2] == other for c in keychain.calls)


# ---- Nothing runs `security` on the event loop except one bounded first read ----

def _join_sync_threads():
    import threading
    for thread in threading.enumerate():
        if thread.name == "gmail-keychain-sync":
            thread.join(5)


def test_a_sign_in_saved_on_the_event_loop_moves_to_keychain_on_a_worker(keychain):
    import threading
    seen = []

    def spying(self, argv, input=None, **kwargs):
        seen.append(threading.current_thread().name)
        return FakeKeychain.__call__(self, argv, input=input, **kwargs)
    keychain.__class__ = type("SpyKeychain", (FakeKeychain,), {"__call__": spying})

    async def sign_in():
        G._save_config({"client_id": "fixture-client", "client_secret": "fixture-secret",
                        "accounts": {ANN: {"refresh_token": TOKEN_A}}})
        return file_data()
    on_disk = asyncio.run(sign_in())
    assert on_disk["accounts"][ANN]["refresh_token"] == TOKEN_A            # on disk first
    _join_sync_threads()
    assert keychain.token(ANN) == TOKEN_A and "refresh_token" not in file_data()["accounts"][ANN]
    assert seen and set(seen) == {"gmail-keychain-sync"}                    # never the loop's thread


def test_a_read_on_the_event_loop_never_waits_for_another_keychain_call(keychain):
    import threading
    import time as clock
    write_file(G.CONFIG_PATH, old_file((ANN, TOKEN_A)))
    G.load_config()
    T.forget_cache()
    held, release = threading.Event(), threading.Event()

    def busy():
        with T._lock:
            held.set()
            release.wait(5)
    worker = threading.Thread(target=busy)
    worker.start()
    held.wait(5)

    async def poll():
        started = clock.monotonic()
        value = T.read(ANN)
        return value, clock.monotonic() - started
    try:
        value, waited = asyncio.run(poll())
    finally:
        release.set()
        worker.join(5)
    assert value is None and waited < 1.0
    assert T.read(ANN) == TOKEN_A                                           # off the loop it reads


def test_store_and_delete_refuse_to_run_on_the_event_loop(keychain):
    async def on_loop():
        return T.store(ANN, TOKEN_A), T.delete(ANN)
    assert asyncio.run(on_loop()) == (False, False)
    assert keychain.calls == []


# ---- A re-sign-in in one app never leaves the other on a stale item ----

def test_after_a_re_sign_in_in_the_other_app_the_stale_item_is_never_used(keychain):
    other = T.OTHER_SERVICES[0]
    # Both apps hold the old token; this run has it cached.
    write_file(G.CONFIG_PATH, old_file((ANN, TOKEN_A)))
    G.load_config()
    keychain.items[(other, T.account_name(ANN))] = TOKEN_A
    assert T.cached(ANN) == TOKEN_A
    # The other app reconnects: only its item and the shared file's fingerprint change.
    keychain.items[(other, T.account_name(ANN))] = TOKEN_B
    raw = file_data()
    raw["accounts"][ANN][G.TOKEN_FINGERPRINT_KEY] = T.fingerprint(TOKEN_B)
    write_file(G.CONFIG_PATH, raw)
    cfg = G.load_config()
    assert G.connected_accounts(cfg)[ANN]["refresh_token"] == TOKEN_B     # not the cached TOKEN_A
    assert keychain.token(ANN) == TOKEN_B                                # our copy is brought up to date
    assert keychain.items[(other, T.account_name(ANN))] == TOKEN_B
    # A refresh that failed with the old token doesn't mark the shared entry expired.
    G._mark_expired(ANN, {"refresh_token": TOKEN_A})
    assert "auth_expired" not in file_data()["accounts"][ANN]
    assert file_data()["accounts"][ANN][G.TOKEN_FINGERPRINT_KEY] == T.fingerprint(TOKEN_B)


def test_no_matching_item_means_no_token_rather_than_a_stale_one(keychain):
    keychain.items[(T.SERVICE, T.account_name(ANN))] = TOKEN_A
    write_file(G.CONFIG_PATH, {"client_id": "fixture-client", "accounts": {ANN: {
        G.TOKEN_STORE_KEY: G.KEYCHAIN, G.TOKEN_FINGERPRINT_KEY: T.fingerprint(TOKEN_B)}}})
    assert ANN not in G.connected_accounts(G.load_config())
    assert G.TOKEN_FINGERPRINT_KEY in file_data()["accounts"][ANN]       # the entry stays


def test_the_fingerprint_is_not_the_token_and_both_apps_compute_it_alike():
    import re
    assert TOKEN_A not in T.fingerprint(TOKEN_A) and len(T.fingerprint(TOKEN_A)) == 64
    source = Path(T.__file__).read_text(encoding="utf-8")
    salt = re.search(r'_FINGERPRINT_SALT = (b"[^"]*")', source).group(1)
    assert not re.search(r"(?i)harbor|quayside", salt)                         # the rename can't change it


def test_a_save_while_a_disconnects_delete_is_pending_keeps_the_token_on_disk(keychain, monkeypatch):
    write_file(G.CONFIG_PATH, old_file((ANN, TOKEN_A)))
    G.load_config()
    assert T.cached(ANN) == TOKEN_A
    G._pending_deletes.add(ANN)            # a disconnect's delete not yet run
    writes = []
    real = G._write_file

    def write(data):
        writes.append(json.loads(json.dumps(data)))
        real(data)
    monkeypatch.setattr(G, "_write_file", write)
    try:
        G._save_config({"client_id": "fixture-client", "client_secret": "fixture-secret",
                        "accounts": {ANN: {"refresh_token": TOKEN_A}}})
    finally:
        G._pending_deletes.discard(ANN)
    assert writes[0]["accounts"][ANN]["refresh_token"] == TOKEN_A          # not the marker alone
    assert keychain.token(ANN) == TOKEN_A


def test_a_file_from_before_fingerprints_never_lets_a_stale_token_win(keychain):
    # An older Keychain build left the marker with no fingerprint; this app's
    # item is stale (TOKEN_A) and the other app's is current (TOKEN_B).
    other = T.OTHER_SERVICES[0]
    keychain.items[(T.SERVICE, T.account_name(ANN))] = TOKEN_A
    keychain.items[(other, T.account_name(ANN))] = TOKEN_B
    write_file(G.CONFIG_PATH, {"client_id": "fixture-client",
                               "accounts": {ANN: {G.TOKEN_STORE_KEY: G.KEYCHAIN, "scopes": [G.SCOPE]}}})
    cfg = G.load_config()
    accounts = G.connected_accounts(cfg)
    assert accounts[ANN]["refresh_token"] == TOKEN_A          # its own item, as before
    accounts[ANN] = {**accounts[ANN], "workspace": False}     # any save from that view
    G._save_config({"client_id": "fixture-client", "client_secret": "", "accounts": accounts})
    entry = file_data()["accounts"][ANN]
    assert G.TOKEN_FINGERPRINT_KEY not in entry and "refresh_token" not in entry
    assert entry[G.TOKEN_STORE_KEY] == G.KEYCHAIN
    # The other app's current item is untouched, so it keeps working.
    assert keychain.items[(other, T.account_name(ANN))] == TOKEN_B
    # The next sign-in here records a fingerprint, and from then on only it counts.
    accounts[ANN] = {**accounts[ANN], "refresh_token": "1//0fake-signed-in-again"}
    G._save_config({"client_id": "fixture-client", "client_secret": "", "accounts": accounts})
    assert file_data()["accounts"][ANN][G.TOKEN_FINGERPRINT_KEY] == T.fingerprint("1//0fake-signed-in-again")


def test_an_unverified_save_keeps_the_fingerprint_the_other_app_just_recorded(keychain):
    other = T.OTHER_SERVICES[0]
    keychain.items[(T.SERVICE, T.account_name(ANN))] = TOKEN_A
    write_file(G.CONFIG_PATH, {"client_id": "fixture-client",
                               "accounts": {ANN: {G.TOKEN_STORE_KEY: G.KEYCHAIN}}})
    accounts = G.connected_accounts(G.load_config())            # TOKEN_A, unverified
    # Meanwhile the other app signs in again and records its token's fingerprint.
    keychain.items[(other, T.account_name(ANN))] = TOKEN_B
    raw = file_data()
    raw["accounts"][ANN][G.TOKEN_FINGERPRINT_KEY] = T.fingerprint(TOKEN_B)
    write_file(G.CONFIG_PATH, raw)
    G._save_config({"client_id": "fixture-client", "client_secret": "", "accounts": accounts})
    assert file_data()["accounts"][ANN][G.TOKEN_FINGERPRINT_KEY] == T.fingerprint(TOKEN_B)
    T.forget_cache()
    assert G.connected_accounts(G.load_config())[ANN]["refresh_token"] == TOKEN_B


def test_a_config_without_google_tokens_never_imports_the_keychain_module(tmp_path):
    # The sealed proof harnesses (tests/test_behavior_availability.py,
    # tests/test_daily_workspace.py) forbid any import outside their list.
    import sys as _sys
    probe = f"""
import json, os, sys
from pathlib import Path
from service import gmail_source as G
home = Path({str(tmp_path)!r})
G.CONFIG_PATH = home / "gmail.json"
G.MOE_DIR = home
assert G.load_config() == {{}} and G.connected_accounts() == {{}}
G._save_config({{"client_id": "fixture-client", "client_secret": "", "accounts": {{}}}})
G.keychain_sync()
(home / "gmail.json").write_text(json.dumps({{"accounts": {{"a@example.test": {{"scopes": []}}}}}}))
G.load_config()
print("service.config.google_tokens" in sys.modules)
"""
    env = {**os.environ, "HARBOR_HOME": str(tmp_path), "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    out = subprocess.run([_sys.executable, "-c", probe], capture_output=True, text=True, env=env,
                         cwd=str(tmp_path), timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().splitlines()[-1] == "False"
