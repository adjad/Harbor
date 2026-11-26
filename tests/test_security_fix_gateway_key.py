"""#20: the phone gateway's link key is new on every `serve` start and removed when it
stops, an approval that arrives with it can't record a standing "always", and no
sandboxed tool or file tool may read the gateway's folder. Invented keys and a
temp HARBOR_HOME only.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from service import credential_pipe, gateway_link
from service.gateway_link import GATEWAY_KEY_HEADER
from service.local_http import AppKeyGuard

APP_KEY = "d4" * 32                         # invented


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HARBOR_HOME", str(tmp_path))
    monkeypatch.setattr(credential_pipe, "_app_key", APP_KEY)
    monkeypatch.setattr(credential_pipe, "_app_launch", True)
    return tmp_path


# ---- #20: one key per serve run ----

def test_each_serve_start_gets_a_new_key(home):
    first = gateway_link.start_secret()
    second = gateway_link.start_secret()
    assert first != second and gateway_link.read_secret() == second


def test_the_key_is_removed_when_the_gateway_stops(home):
    key = gateway_link.start_secret()
    gateway_link.end_secret(key)
    assert not gateway_link.secret_path().exists()


def test_stopping_an_older_run_leaves_a_newer_runs_key(home):
    old = gateway_link.start_secret()
    new = gateway_link.start_secret()
    gateway_link.end_secret(old)
    assert gateway_link.read_secret() == new


def test_serve_rotates_and_cleans_up(home, monkeypatch):
    from service.phone_gateway import __main__ as cli
    stale = gateway_link.ensure_secret()
    seen = {}

    class FakeServer:
        def __init__(self, config):
            pass

        def run(self):
            seen["during"] = gateway_link.read_secret()

    import uvicorn
    monkeypatch.setattr(uvicorn, "Server", FakeServer)
    monkeypatch.setattr(cli.tailscale, "resolve_bind_host", lambda host: "127.0.0.1")
    monkeypatch.setattr(cli, "_computer_name", lambda: "Fixture Mac")
    monkeypatch.setattr(cli, "state_dir", lambda: home / "phone_gateway")
    from service.phone_gateway import app as gateway_app
    monkeypatch.setattr(gateway_app, "create_app", lambda **kwargs: object())
    assert cli.main(["serve", "--host", "127.0.0.1"]) == 0
    assert seen["during"] not in (None, stale)
    assert not gateway_link.secret_path().exists()


# ---- #20: no standing "always" through the gateway ----

def _guarded():
    reached = []

    async def app(scope, receive, send):
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        reached.append(body)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    return TestClient(AppKeyGuard(app), base_url="http://127.0.0.1:8765"), reached


def _approve(client, headers, scope):
    return client.post("/agent/approve", headers=headers,
                       json={"session_id": "fixture", "action_id": "a1", "approved": True, "scope": scope})


def test_gateway_key_cannot_record_always(home):
    key = gateway_link.ensure_secret()
    client, reached = _guarded()
    assert _approve(client, {GATEWAY_KEY_HEADER: key}, "always").status_code == 403
    assert not reached


def test_gateway_key_still_answers_once_and_the_body_reaches_the_app(home):
    key = gateway_link.ensure_secret()
    client, reached = _guarded()
    assert _approve(client, {GATEWAY_KEY_HEADER: key}, "once").status_code == 200
    assert reached and b'"scope": "once"' in reached[0].replace(b'":"', b'": "')


def test_the_app_key_may_still_record_always(home):
    client, reached = _guarded()
    assert _approve(client, {"x-harbor-app-key": APP_KEY}, "always").status_code == 200


def test_the_gateway_turns_a_phone_always_into_once():
    from service.phone_gateway.app import _build_approve_body
    body = _build_approve_body({"session_id": "fixture", "action_id": "a1",
                                "approved": True, "scope": "always"})
    assert body["scope"] == "once"


# ---- #20: the gateway's folder is never readable by tools ----

def test_the_skill_sandbox_never_reads_the_gateway_folder(home, tmp_path):
    from service.paths import MOE_DIR
    from service.skills import sandbox
    skill = tmp_path / "skills" / "fixture"
    skill.mkdir(parents=True)
    profile = sandbox.build_profile(skill, [Path.home()], [])
    deny = f'(deny file-read* (subpath "{MOE_DIR / "phone_gateway"}"))'
    assert deny in profile
    assert profile.index(deny) > profile.index(f'(allow file-read* (subpath "{Path.home()}"))')


@pytest.mark.parametrize("tool,category,args", [
    ("read_file", "fs_read", {"path": "{moe}/phone_gateway/gateway-link.key"}),
    ("write_file", "fs_write", {"path": "{moe}/phone_gateway/devices.json", "content": "{}"}),
    ("run_shell", "shell", {"cmd": "cat {moe}/phone_gateway/gateway-link.key"}),
])
def test_tools_ask_before_touching_the_gateway_folder(home, monkeypatch, tool, category, args):
    from service.paths import MOE_DIR
    from service.safety import policy
    monkeypatch.setattr(policy, "_FULL_ACCESS", True)
    args = {k: v.replace("{moe}", str(MOE_DIR)) for k, v in args.items()}
    assert policy.decide(category, args, tool=tool).tier is not policy.Tier.ALLOW
