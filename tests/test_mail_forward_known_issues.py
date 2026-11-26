"""Known issue #4, the Mail forward:
- a forward that stopped at one of its own checks before `send` says nothing was
  sent (not "outcome unknown"), while every other failure stays unknown;
- a note on an email with attachments is refused before anything is sent, and the
  card says before approval that a note makes the forward plain text;
- a forward Mail refused to send is closed, never left open (the script contract).
Never real Mail: the sandbox app behind the real outbox, or a scripted answer.
Every address and message is invented.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from tests.test_mail_forward import FRIEND, SandboxApp, forward, no_real_effects  # noqa: F401
from service.assistant import outbox
from service.tools import action_tools as A
from service.tools import registry as R

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = (ROOT / "app/Sources/HarborApp/MailReplyScript.swift").read_text(encoding="utf-8")
SENDER = (ROOT / "app/Sources/HarborApp/OutboundSender.swift").read_text(encoding="utf-8")


def _wire(monkeypatch, sandbox):
    monkeypatch.setattr(outbox, "hub", sandbox)
    real = outbox.request

    async def request(operation, payload):
        return await real(operation, payload, timeout=5)
    monkeypatch.setattr(A, "app_request", request)


@pytest.mark.parametrize("check, words", [
    ("lost_original", "didn't keep the original"),
    ("recipients_changed", "changed the forward's recipients"),
    ("identity_changed", "sending address outside"),
    ("not_found", "couldn't find that email"),
    ("note_drops_attachments", "without a note"),
])
def test_a_stop_at_a_check_before_send_says_nothing_was_sent(monkeypatch, tmp_path, check, words):
    sandbox = SandboxApp(tmp_path, answer=lambda body: {
        "ok": False, "error": "native text the backend never repeats",
        "refusal": "checked_before_send", "check": check})
    _wire(monkeypatch, sandbox)
    result = forward(sandbox.inbox_email()["message_id"], FRIEND)
    assert type(result) is R._NotSentResult
    assert "not sent" in result.lower() and "nothing was sent" in result.lower() and words in result
    assert "native text" not in result


@pytest.mark.parametrize("answer", [
    # A refused send: Mail may still hold it, so it stays unknown.
    {"ok": False, "error": "Mail did not accept the forward for sending and its window was closed; "
                           "check Mail's Outbox before retrying."},
    # A check name the backend doesn't know, or the marker with a status, proves nothing.
    {"ok": False, "error": "x", "refusal": "checked_before_send", "check": "something_else"},
    {"ok": False, "error": "x", "refusal": "checked_before_send", "check": ["not_found"]},
    {"ok": False, "status": "failed", "error": "x", "refusal": "checked_before_send", "check": "not_found"},
])
def test_any_other_failure_stays_unknown(monkeypatch, tmp_path, answer):
    sandbox = SandboxApp(tmp_path, answer=lambda body: dict(answer))
    _wire(monkeypatch, sandbox)
    result = forward(sandbox.inbox_email()["message_id"], FRIEND)
    assert type(result) is R._OutboundUnknownResult


def test_the_marker_counts_only_for_a_forward():
    marked = {"ok": False, "error": "x", "refusal": "checked_before_send", "check": "not_found"}
    assert outbox._pre_send_refusal("forward_email", marked)
    for other in ("send_email", "reply_to_email", "send_message"):
        assert outbox._pre_send_refusal(other, marked) is None


def test_a_note_on_an_email_with_attachments_is_refused_before_sending(monkeypatch, tmp_path):
    sandbox = SandboxApp(tmp_path)
    _wire(monkeypatch, sandbox)
    email = sandbox.inbox_email()
    email["attachments"] = [{"name": "fixture-report.pdf"}]
    result = forward(email["message_id"], FRIEND, note="For you")
    assert type(result) is R._NotSentResult and "without a note" in result
    assert sandbox.forwards() == []
    # Without a note it goes, attachments and all (Mail's own forward).
    assert forward(email["message_id"], FRIEND) == f"Forwarded to {FRIEND}."


def test_the_card_says_a_note_makes_the_forward_plain_text():
    with_note = A.confirm_preview("forward_email", {"message_id": "<fixture@example.test>",
                                                   "to": FRIEND, "note": "For you"})
    assert A.FORWARD_NOTE_IS_PLAIN in with_note
    bare = A.confirm_preview("forward_email", {"message_id": "<fixture@example.test>", "to": FRIEND})
    assert A.FORWARD_NOTE_IS_PLAIN not in bare and "formatting and attachments" in bare


def _forward_script() -> str:
    start = SCRIPTS.index("static func forwardEmail(")
    return SCRIPTS[start:SCRIPTS.index("static func decodeForward", start)]


def test_the_script_closes_a_forward_mail_refused_to_send():
    script = _forward_script()
    after = script[script.index("if sendAnswer is not true then"):]
    assert after.index("close fwd saving no") < after.index('error "\\(forwardSendNotAccepted)"')
    assert "check Mail" in re.search(r'static let forwardSendNotAccepted = "([^"]+)"', SCRIPTS).group(1)


def test_only_checks_before_send_carry_the_number_and_the_app_passes_them_on():
    script = _forward_script()
    send_at = script.index("set sendAnswer to (send)")
    numbered = [m.start() for m in re.finditer(r"number \\\(checkedBeforeSendNumber\)", script)]
    assert numbered and all(at < send_at for at in numbered)
    assert "mail attachments of theMsg" in script
    body = SENDER[SENDER.index("static func forwardEmail("):]
    body = body[:body.index("static func flagEmail(")]
    assert "MailActionScript.preSendCheck(" in body and "refusal(code).merging(checked)" in body
