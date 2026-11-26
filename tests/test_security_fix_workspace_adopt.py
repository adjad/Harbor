"""Auditor note M1: what the Google Workspace server left in its credential files is
folded back into the account store only when it is newer than what is stored, and
only with scopes the user currently allows.

- Harbor records, beside the files (never in them), a fingerprint of the token it
  wrote for each account. A file's token is adopted only while the stored token is
  still that one: after a newer sign-in (in either app) a left-over file is stale
  and is discarded, never written over the new sign-in.
- A file that lists a consent scope the account's current grant lacks (a reconnect
  that unticked Calendar) is discarded: its token came from a broader grant.
- A file with no record of being written by Harbor is discarded.
- A crashed run's refreshed token is still kept when the record proves it newer,
  including at startup, when the token can't be read yet: its file then waits for
  a read instead of being discarded.
- The server's sign-in files go whenever its process stops, even when its output
  closes before the process has exited.
Invented accounts and token strings only; nothing reaches the real Keychain.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from service import gmail_source, google_workspace as W

ACCOUNT = "fixture.person@example.invalid"
SEND = "https://www.googleapis.com/auth/gmail.send"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "CREDENTIALS_DIR", tmp_path / "google_workspace" / "credentials")
    accounts = {ACCOUNT: {"client_id": "fixture-client", "client_secret": "fixture-secret",
                          "refresh_token": "fixture-refresh-one",
                          "scopes": [gmail_source.SCOPE, W.FULL_SCOPE], "workspace": True}}
    state = {"readable": True}
    monkeypatch.setattr(gmail_source, "connected_accounts",
                        lambda cfg=None: json.loads(json.dumps(accounts)) if state["readable"] else {})
    monkeypatch.setattr(gmail_source, "named_accounts", lambda: set(accounts))

    def adopt(updates):
        for email, item in updates.items():
            accounts[email].update(item)
        return len(updates)
    monkeypatch.setattr(gmail_source, "adopt_workspace_updates", adopt)
    monkeypatch.setattr(W, "_server_live", lambda: False)
    return accounts, state


def _server_writes(**change):
    path = W.credentials_path(ACCOUNT)
    data = json.loads(path.read_text())
    data.update(change)
    path.write_text(json.dumps(data))


def _files():
    return sorted(W.CREDENTIALS_DIR.glob("*.json")) if W.CREDENTIALS_DIR.is_dir() else []


def test_a_left_over_file_never_overwrites_a_newer_sign_in(store):
    accounts, _ = store
    W.start_server_credentials()
    _server_writes(refresh_token="fixture-refresh-two")
    # The run crashes; the user then signs in again (here or in the other app).
    accounts[ACCOUNT]["refresh_token"] = "fixture-refresh-new"
    W.sync_credentials(accounts)                   # startup: the server isn't running
    assert accounts[ACCOUNT]["refresh_token"] == "fixture-refresh-new"
    assert _files() == []


def test_a_file_never_brings_back_a_scope_the_reconnect_unticked(store):
    accounts, _ = store
    W.start_server_credentials()
    _server_writes(refresh_token="fixture-refresh-two",
                   scopes=[gmail_source.SCOPE, gmail_source.CALENDAR_SCOPE, W.FULL_SCOPE])
    W.stop_server_credentials()
    assert gmail_source.CALENDAR_SCOPE not in accounts[ACCOUNT]["scopes"]
    assert accounts[ACCOUNT]["refresh_token"] == "fixture-refresh-one"
    assert _files() == []


def test_a_file_harbor_never_wrote_is_discarded(store):
    accounts, _ = store
    W.CREDENTIALS_DIR.mkdir(parents=True)
    (W.CREDENTIALS_DIR / f"{ACCOUNT}.json").write_text(json.dumps(
        {"refresh_token": "fixture-refresh-planted", "scopes": [W.FULL_SCOPE]}))
    W.sync_credentials(accounts)
    assert accounts[ACCOUNT]["refresh_token"] == "fixture-refresh-one"
    assert _files() == []


def test_a_crashed_runs_newer_token_and_on_demand_scope_are_kept(store):
    accounts, _ = store
    W.start_server_credentials()
    _server_writes(refresh_token="fixture-refresh-two",
                   scopes=[gmail_source.SCOPE, W.FULL_SCOPE, SEND])
    W.sync_credentials(accounts)                   # the next launch, after a crash
    assert accounts[ACCOUNT]["refresh_token"] == "fixture-refresh-two"
    assert SEND in accounts[ACCOUNT]["scopes"]
    assert _files() == [] and not W._written_path().exists()


def test_at_startup_an_unreadable_token_keeps_its_file_for_a_later_read(store):
    accounts, state = store
    W.start_server_credentials()
    _server_writes(refresh_token="fixture-refresh-two")
    state["readable"] = False                      # Keychain not read yet (event loop)
    W.sync_credentials({})
    assert [p.stem for p in _files()] == [ACCOUNT]
    assert accounts[ACCOUNT]["refresh_token"] == "fixture-refresh-one"
    state["readable"] = True
    W.start_server_credentials()                   # off the loop: decided now
    assert accounts[ACCOUNT]["refresh_token"] == "fixture-refresh-two"
    W.stop_server_credentials()
    assert _files() == []


def test_a_disconnected_accounts_file_is_discarded(store, monkeypatch):
    accounts, _ = store
    W.start_server_credentials()
    monkeypatch.setattr(gmail_source, "named_accounts", lambda: set())
    monkeypatch.setattr(gmail_source, "connected_accounts", lambda cfg=None: {})
    W.sync_credentials({})
    assert _files() == []


def test_the_files_go_when_the_server_output_closes_before_it_exits(monkeypatch, tmp_path):
    from service import mcp
    retired = []

    async def retire():
        retired.append(True)
    monkeypatch.setattr(mcp, "_retire_workspace_credentials", retire)

    async def run():
        server = mcp.MCPServer(mcp._WORKSPACE_SERVER, {})
        # Closes its output at once, and exits a moment later.
        server.proc = await asyncio.create_subprocess_exec(
            "/bin/sh", "-c", "exec 1>&-; sleep 0.3",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
        await asyncio.wait_for(server._read_loop(), 10)
        return server
    server = asyncio.run(run())
    assert retired, "the sign-in files outlived the server"
    assert not server.running
