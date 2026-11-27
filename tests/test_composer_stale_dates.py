"""The Brief's email composer never writes an old email's "today" as if it were
today. Every name, address and message here is invented."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from service.assistant import email_reply_api as api
from service.proactive import cycle, draft_context

TZ = "America/Chicago"
ME = "robin@example.edu"


def _row(days_ago):
    sent = datetime.fromtimestamp(time.time(), ZoneInfo(TZ)).replace(hour=9, minute=0).timestamp() - days_ago * 86400
    return {"message_id": "m1@example.org", "account": "Home", "account_id": "", "subject": "Community meeting",
            "sender": "Alex Rivera <alex@example.org>", "to": ME, "ts": sent,
            "body": "Hi Robin, the community meeting is at 5 PM today. Can you make it?"}


@pytest.fixture
def mail(monkeypatch):
    rows = []
    monkeypatch.setattr(api, "_mail_rows", lambda: [dict(r) for r in rows])
    monkeypatch.setattr(api, "_header_rows", lambda: [])
    monkeypatch.setattr(api, "_native_accounts", lambda: [])
    monkeypatch.setattr(draft_context, "my_addresses", lambda: {ME})
    monkeypatch.setattr(draft_context, "on_device", lambda: False)
    monkeypatch.setattr(cycle, "_zone", lambda: TZ)
    return rows


class Model:
    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    async def __call__(self, system, material):
        self.prompts.append(material)
        return self.replies.pop(0) if self.replies else ""


def draft(mail, monkeypatch, days_ago, *replies):
    mail.append(_row(days_ago))
    model = Model(*replies)
    monkeypatch.setattr(api, "_model_reply", model)
    out = asyncio.run(api.draft_reply({"message_id": "m1@example.org", "account": "Home", "account_id": ""}))
    return out, model


STALE = "Hi Alex, I can make it to the community meeting at 5 PM today."
NAMED = "Hi Alex, sorry I missed the community meeting on Monday. Can you share the notes?"


def test_the_composer_names_the_day_an_old_email_was_sent(mail, monkeypatch):
    out, model = draft(mail, monkeypatch, 3, NAMED)
    assert out["draft"] == NAMED and len(model.prompts) == 1
    assert "The message to answer was sent on" in model.prompts[0]
    assert "Never write today, tonight or tomorrow" in model.prompts[0]


def test_a_composer_draft_saying_today_about_an_old_email_is_asked_again(mail, monkeypatch):
    out, model = draft(mail, monkeypatch, 3, STALE, NAMED)
    assert out["draft"] == NAMED and len(model.prompts) == 2


def test_a_composer_draft_that_keeps_saying_today_leaves_the_box_empty(mail, monkeypatch):
    out, _model = draft(mail, monkeypatch, 3, STALE, STALE)
    assert out["draft"] == "" and out["draft_error"]


def test_an_email_from_today_gets_no_note_and_may_say_today(mail, monkeypatch):
    out, model = draft(mail, monkeypatch, 0, STALE)
    assert out["draft"] == STALE and len(model.prompts) == 1
    assert "The message to answer was sent on" not in model.prompts[0]
