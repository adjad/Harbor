"""Forwarding a chosen email through Mail: approval card first, "Forwarded" only on Mail's receipt.

Never real Mail: the app is the sandbox consumer (sandbox/outbound.py, which
mirrors OutboundSender.forwardEmail) behind the real outbox, or a scripted
answer. Every address and message is invented.
"""
from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import tempfile
from pathlib import Path

import pytest

_scratch = tempfile.TemporaryDirectory(prefix="harbor-forward-")
os.environ.setdefault("HARBOR_HOME", _scratch.name)

import service.main  # noqa: E402,F401 — registers every tool
from sandbox.outbound import OutboundConsumer  # noqa: E402
from sandbox.world import World  # noqa: E402
from service.assistant import outbox  # noqa: E402
from service.safety import policy  # noqa: E402
from service.tools import action_tools as A  # noqa: E402
from service.tools import registry as R  # noqa: E402

FRIEND = "pat.fixture@example.test"
OTHER = "lee.fixture@example.test"


@pytest.fixture(autouse=True)
def no_real_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("no network or native process in forward tests")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(A, "_own_address_guard", lambda *a: "")
    monkeypatch.setattr(A, "_unreviewed", lambda: False)
    yield
    assert not outbox._pending and not outbox._pending_waiters


class SandboxApp:
    """The real outbox publishing to the sandbox app, which posts its result back."""
    has_subscribers = True

    def __init__(self, tmp_path: Path, *, answer=None):
        self.world = World(path=tmp_path / "world.json", seed=7)
        self.events: list[dict] = []
        self.answer = answer
        app = self

        class Client:
            async def post(self, path, json=None):
                assert path == "/assistant/action_result"
                body = dict(json)
                if app.answer is not None:
                    body = {"action_id": body["action_id"], **app.answer(body)}
                assert outbox.complete(body["action_id"], body)

        self.consumer = OutboundConsumer(self.world, Client())

    async def publish(self, event, **metadata):
        self.events.append(dict(event))
        await self.consumer._dispatch(dict(event))
        return event

    def inbox_email(self) -> dict:
        return next(e for e in self.world.state["emails"].values() if e["mailbox"] == "inbox")

    def forwards(self) -> list[dict]:
        return [e for e in self.world.state["emails"].values() if e.get("forwarded_from")]


@pytest.fixture
def app(monkeypatch, tmp_path):
    sandbox = SandboxApp(tmp_path)
    monkeypatch.setattr(outbox, "hub", sandbox)
    real = outbox.request

    async def request(operation, payload):
        return await real(operation, payload, timeout=5)
    monkeypatch.setattr(A, "app_request", request)
    return sandbox


def forward(message_id, to, note=""):
    return asyncio.run(R.run_tool(R.get_tool("forward_email"),
                                  {"message_id": message_id, "to": to, "note": note}))


def test_a_forward_always_goes_through_the_approval_card():
    tool = R.get_tool("forward_email")
    assert tool.category in policy.ALWAYS_CONFIRM_CATEGORIES
    args = {"message_id": "<fixture-1@example.test>", "to": FRIEND, "note": "FYI"}
    assert policy.decide(tool.category, args, tool="forward_email").tier is policy.Tier.CONFIRM
    card = A.confirm_preview("forward_email", args)
    assert f"To: {FRIEND}" in card and "Note:\nFYI" in card
    assert "Forward message <fixture-1@example.test>" in card


def test_an_approved_forward_reaches_mail_once_and_says_forwarded(app):
    original = app.inbox_email()
    result = forward(original["message_id"], f"{FRIEND}, {OTHER}", "FYI, see below")
    assert result == f"Forwarded to {FRIEND}, {OTHER}."
    assert R.classify_tool_outcome("forward_email", result).status == "succeeded"
    assert [e["type"] for e in app.events] == ["forward_email"]
    assert app.events[0]["to"] == [FRIEND, OTHER] and app.events[0]["body"] == "FYI, see below"
    [sent] = app.forwards()
    assert sent["to"] == [FRIEND, OTHER] and sent["forwarded_from"] == original["message_id"]
    # The note sits above the original, which is still there.
    assert sent["body"].startswith("FYI, see below\n\n") and original["body"] in sent["body"]


def test_a_forward_without_a_note_keeps_the_original_alone(app):
    original = app.inbox_email()
    assert forward(original["message_id"], FRIEND) == f"Forwarded to {FRIEND}."
    [sent] = app.forwards()
    assert sent["body"].startswith("Begin forwarded message:") and original["body"] in sent["body"]


def test_a_missing_email_is_never_reported_forwarded(app):
    result = forward("<not-in-the-inbox@example.test>", FRIEND)
    assert R.classify_tool_outcome("forward_email", result).status == "unknown"
    assert not str(result).startswith("Forwarded") and app.forwards() == []


@pytest.mark.parametrize("answer", [
    # Mail answered but gave no receipt (an older app, or a script that returned early).
    lambda body: {"ok": True, "error": ""},
    # A receipt for different recipients than the card showed.
    lambda body: {"ok": True, "error": "", "forward": {**body["forward"], "to": [OTHER]}},
    # A receipt for a different email.
    lambda body: {"ok": True, "error": "",
                  "forward": {**body["forward"], "message_id": "<another@example.test>"}},
    # Malformed receipts.
    lambda body: {"ok": True, "error": "", "forward": {**body["forward"], "to": []}},
    lambda body: {"ok": True, "error": "", "forward": "Forwarded"},
    # Mail said no after the event reached it (a check before send, or send answered false).
    lambda body: {"ok": False, "error": "Mail did not keep the original email in the forward. Nothing sent."},
])
def test_without_mails_matching_receipt_the_outcome_is_unknown_not_forwarded(monkeypatch, tmp_path, answer):
    sandbox = SandboxApp(tmp_path, answer=answer)
    monkeypatch.setattr(outbox, "hub", sandbox)
    real = outbox.request

    async def request(operation, payload):
        return await real(operation, payload, timeout=5)
    monkeypatch.setattr(A, "app_request", request)
    result = forward(sandbox.inbox_email()["message_id"], FRIEND)
    assert type(result) is R._OutboundUnknownResult
    assert R.classify_tool_outcome("forward_email", result).status == "unknown"
    low = str(result).lower()
    assert not low.startswith("forwarded") and "check mail" in low and "retry" in low


def test_a_receipt_matches_addresses_regardless_of_case(monkeypatch, tmp_path):
    sandbox = SandboxApp(tmp_path, answer=lambda body: {
        "ok": True, "error": "", "forward": {**body["forward"], "to": [FRIEND.upper()],
                                             "message_id": body["forward"]["message_id"].upper()}})
    monkeypatch.setattr(outbox, "hub", sandbox)
    real = outbox.request

    async def request(operation, payload):
        return await real(operation, payload, timeout=5)
    monkeypatch.setattr(A, "app_request", request)
    assert forward(sandbox.inbox_email()["message_id"], FRIEND) == f"Forwarded to {FRIEND}."


def test_mail_automation_refused_is_proof_nothing_was_sent(monkeypatch, tmp_path):
    sandbox = SandboxApp(tmp_path, answer=lambda body: {
        "ok": False, "error": "Mail Automation access isn't granted", "refusal": "automation_denied"})
    monkeypatch.setattr(outbox, "hub", sandbox)
    real = outbox.request

    async def request(operation, payload):
        return await real(operation, payload, timeout=5)
    monkeypatch.setattr(A, "app_request", request)
    result = forward(sandbox.inbox_email()["message_id"], FRIEND)
    assert type(result) is R._NotSentResult and "not sent" in result.lower()
    assert "Turn on Mail for Harbor" in result


def test_no_app_connected_sends_nothing(monkeypatch, tmp_path):
    sandbox = SandboxApp(tmp_path)
    sandbox.has_subscribers = False
    monkeypatch.setattr(outbox, "hub", sandbox)
    real = outbox.request

    async def request(operation, payload):
        return await real(operation, payload, timeout=1)
    monkeypatch.setattr(A, "app_request", request)
    result = forward(sandbox.inbox_email()["message_id"], FRIEND)
    assert type(result) is R._NotSentResult and sandbox.events == []
