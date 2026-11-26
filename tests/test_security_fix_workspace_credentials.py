"""Known issue #1: the Google Workspace server's credential files exist only while
that server runs. They are written fresh from the account store (Keychain) when it
starts, in a 0700 folder with 0600 files, and removed when it stops or dies; a
start after a crash sweeps what was left. Anything the server changed in them (a
refreshed token, a scope it added on demand) is folded back into the account store
first, so no grant is lost. Invented accounts and token strings only; the account
store is replaced, nothing reaches the real Keychain.
"""
from __future__ import annotations

import asyncio
import json
import os

import pytest

from service import gmail_source, google_workspace as W

ACCOUNT = "fixture.person@example.invalid"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "CREDENTIALS_DIR", tmp_path / "google_workspace" / "credentials")
    accounts = {ACCOUNT: {"client_id": "fixture-client", "client_secret": "fixture-secret",
                          "refresh_token": "fixture-refresh-one", "scopes": [W.FULL_SCOPE],
                          "workspace": True}}
    adopted = []
    monkeypatch.setattr(gmail_source, "connected_accounts", lambda cfg=None: json.loads(json.dumps(accounts)))

    def adopt(updates):
        adopted.append(updates)
        for email, item in updates.items():
            accounts[email].update(item)
        return len(updates)

    monkeypatch.setattr(gmail_source, "adopt_workspace_updates", adopt)
    monkeypatch.setattr(gmail_source, "named_accounts", lambda: set(accounts))
    monkeypatch.setattr(W, "_server_live", lambda: False)
    return accounts, adopted


def _files():
    return sorted(W.CREDENTIALS_DIR.glob("*.json")) if W.CREDENTIALS_DIR.is_dir() else []


def test_no_credential_file_while_the_server_isnt_running(store):
    accounts, _ = store
    W.sync_credentials(accounts)              # what startup and a sign-in call
    assert _files() == []


def test_a_running_server_gets_private_files_and_they_go_when_it_stops(store):
    W.start_server_credentials()
    files = _files()
    assert [p.stem for p in files] == [ACCOUNT]
    assert oct(W.CREDENTIALS_DIR.stat().st_mode & 0o777) == "0o700"
    assert oct(files[0].stat().st_mode & 0o777) == "0o600"
    W.stop_server_credentials()
    assert _files() == [] and not W.CREDENTIALS_DIR.exists()


def test_what_the_server_changed_is_kept_before_its_files_go(store):
    accounts, adopted = store
    W.start_server_credentials()
    path = _files()[0]
    data = json.loads(path.read_text())
    data["refresh_token"] = "fixture-refresh-two"
    data["scopes"] = [W.FULL_SCOPE, "https://www.googleapis.com/auth/gmail.send"]
    path.write_text(json.dumps(data))
    W.stop_server_credentials()
    assert accounts[ACCOUNT]["refresh_token"] == "fixture-refresh-two"
    assert "https://www.googleapis.com/auth/gmail.send" in accounts[ACCOUNT]["scopes"]
    assert _files() == []


def test_a_start_after_a_crash_sweeps_and_keeps_the_left_over_changes(store):
    accounts, _ = store
    W.start_server_credentials()              # an earlier run, which then crashed
    os.chmod(W.CREDENTIALS_DIR, 0o755)
    left = W.CREDENTIALS_DIR / f"{ACCOUNT}.json"
    left.write_text(json.dumps({"refresh_token": "fixture-refresh-left", "scopes": [W.FULL_SCOPE]}))
    os.chmod(left, 0o644)
    (W.CREDENTIALS_DIR / "gone.person@example.invalid.json").write_text("{}")
    W.sync_credentials(accounts)              # startup: the server isn't running yet
    assert accounts[ACCOUNT]["refresh_token"] == "fixture-refresh-left"
    assert _files() == []


def test_the_mcp_server_wires_both_ends(monkeypatch):
    import inspect
    from service import mcp
    server = inspect.getsource(mcp.MCPServer)
    assert "start_server_credentials" in server and "_retire_workspace_credentials" in server
    assert "stop_server_credentials" in inspect.getsource(mcp._retire_workspace_credentials)
