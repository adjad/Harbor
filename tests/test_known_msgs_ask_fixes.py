"""Known issues from the 0.1.8 notes: stale send block, reminder card heading,
suggestion held back while an approval card waits."""
from __future__ import annotations

import time
from pathlib import Path

from service.memory.store import SessionStore
from service.safety import policy
from service.tools import action_tools

ROOT = Path(__file__).resolve().parents[1]


def _store(tmp_path):
    return SessionStore(tmp_path / "memory.db")


def test_not_delivered_line_is_plain_and_not_the_old_quiet_text():
    assert action_tools.QUIET_NOT_DELIVERED == "Not delivered"
    client = (ROOT / "app/Sources/HarborApp/HarborClient.swift").read_text(encoding="utf-8")
    assert 'static let quietNotDelivered = "Not delivered"' in client


def test_a_chat_blocked_by_an_old_failed_text_is_unblocked_on_open(tmp_path):
    path = tmp_path / "memory.db"
    store = SessionStore(path)
    sid = store.create_session()
    claim = store.claim_model_effect(sid, "req-1", "send_message", {"to": "x", "text": "hi"}, outbound=True)
    assert claim["admitted"]
    store.finish_model_effect(sid, claim, verified=False)         # an old build: left "uncertain"
    assert store.unresolved_action(sid) is not None
    # Fresh hold stays: a recent unknown outcome must still be checked.
    assert SessionStore(path).unresolved_action(sid) is not None
    old = time.time() - SessionStore.STALE_SEND_BLOCK_SECONDS - 60
    store._db.execute("UPDATE workflows SET updated_at=?", (old,))
    store._db.commit()
    reopened = SessionStore(path)
    assert reopened.unresolved_action(sid) is None
    again = reopened.claim_model_effect(sid, "req-2", "send_message", {"to": "x", "text": "hi"}, outbound=True)
    assert again["admitted"]


def test_an_old_hold_on_another_tool_is_left_alone(tmp_path):
    path = tmp_path / "memory.db"
    store = SessionStore(path)
    sid = store.create_session()
    claim = store.claim_model_effect(sid, "req-1", "send_email", {"to": "x"}, outbound=True)
    store.finish_model_effect(sid, claim, verified=False)
    store._db.execute("UPDATE workflows SET updated_at=?", (time.time() - 10 * 86400,))
    store._db.commit()
    assert SessionStore(path).unresolved_action(sid) is not None


def test_the_reminder_delete_card_says_reminders():
    d = policy.decide("calendar_write", {"scope": "today"}, "clear_reminders")
    assert d.tier is policy.Tier.CONFIRM
    assert "Reminders" in d.reason and "calendar" not in d.reason
    other = policy.decide("calendar_write", {"title": "Lunch"}, "cancel_event")
    assert "calendar" in other.reason


def test_a_suggestion_is_held_while_an_approval_card_waits():
    src = (ROOT / "app/Sources/HarborApp/NudgeViews.swift").read_text(encoding="utf-8")
    assert "approvalWaiting: model.pending != nil" in src
    assert "nudgeCount > 0 && !approvalWaiting" in src
