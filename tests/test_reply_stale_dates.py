"""A reply draft never restates an old email's "today" as if it were today, and
no reply card accepts a meeting that is over. Every name, address and message
here is invented."""
from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from service.proactive import draft_context, prepare, stable

TZ = "America/Chicago"
NOW = datetime(2026, 11, 26, 10, 0, tzinfo=ZoneInfo(TZ)).timestamp()      # a Thursday
DAY = 86400
MONDAY_9AM = datetime(2026, 11, 23, 9, 0, tzinfo=ZoneInfo(TZ)).timestamp()
MONDAY_5PM = datetime(2026, 11, 23, 17, 0, tzinfo=ZoneInfo(TZ)).timestamp()
SCHED = {"calendar": [], "reminders": []}


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(prepare, "_user_names", lambda: [])
    monkeypatch.setattr(draft_context, "on_device", lambda: False)


def freeze(ref):
    return {"ok": True, "can_send": True, "envelope": "sealed", "to": ["alex@example.org"], "subject": "Re: x"}


def judged(n, *, subject, text, received, kind="reply", due_ts=None, has_clock=False, title="Reply to Alex"):
    raw = {"id": f"mail:{n}", "source": "mail", "person": True, "title": subject, "text": text,
           "received_ts": received, "sender_address": "alex@example.org", "ref": {},
           "reply_ref": {"message_id": f"m{n}@example.org", "account": "Home", "account_id": ""}}
    return {"item": raw, "who": "Alex Rivera", "title": title, "source": "mail", "kind": kind,
            "primary": f"mail:{n}", "account": "home", "due_ts": due_ts, "has_clock": has_clock,
            "asks": [f"ask:mail:{n}@1"], "fingerprints": [f"mail:{n}:1"], "urgency": "high", "why": ""}


class Model:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    async def __call__(self, system, material, abort, tokens):
        self.calls.append((system, material))
        return self.replies.pop(0) if self.replies else ""


def run(items, model=None, drafts=None):
    return asyncio.run(prepare.prepare(items, SCHED, model or Model(), None, now=NOW, tz=TZ, freeze=freeze,
                                       drafts=drafts))[1]


MEETING = dict(subject="Community meeting", text="Hi! Can you make the community meeting?", received=MONDAY_9AM)
GALA = dict(subject="Re: Invitation to the fall gala", text="Would you like to come to the fall gala?",
            received=NOW - 2 * DAY)


# --- a meeting that is over --------------------------------------------------------------


def test_no_reply_card_accepts_a_meeting_that_is_over():
    over = judged(1, **MEETING, kind="meet", due_ts=MONDAY_5PM, has_clock=True)
    assert [c["sources"][0]["ref"] for c in run([over, judged(2, **GALA)])] == ["mail:2"]   # only the gala
    assert run([over]) == []
    day_over = judged(1, **MEETING, kind="meet", due_ts=MONDAY_9AM, has_clock=False)      # "meet Monday"
    assert run([day_over]) == []


def test_a_late_reply_with_no_event_time_still_gets_its_card():
    owed = judged(1, **MEETING, due_ts=MONDAY_5PM - 8 * 3600, has_clock=False)    # "reply by Monday"
    cards = run([owed])
    assert len(cards) == 1 and "expires_ts" not in cards[0]


def test_a_reply_owed_by_a_clock_time_still_gets_its_late_card():
    owed = judged(1, **MEETING, due_ts=MONDAY_5PM, has_clock=True)                  # "reply by 5 PM Monday"
    cards = run([owed])
    assert len(cards) == 1 and "expires_ts" not in cards[0]
    assert prepare.expires_at(owed, TZ) is None and not prepare.event_passed(owed, NOW, TZ)


def test_a_kept_reply_owed_by_a_clock_time_stays(same_ask):
    assert [c["id"] for c in _carry(_old(NAMED, due_ts=MONDAY_5PM))] == ["card:a"]


def test_the_template_never_says_today():
    for method in ("email_reply", "text_reply"):
        words = prepare.template_draft(judged(1, **MEETING), method)
        assert not prepare.stale_relative_day(words, judged(1, **MEETING), NOW, TZ)


def test_a_meeting_still_ahead_records_when_it_ends():
    ahead = judged(1, **MEETING, kind="meet", due_ts=NOW + 7 * 3600, has_clock=True)
    assert run([ahead])[0]["expires_ts"] == NOW + 7 * 3600


# --- the draft's dates ---------------------------------------------------------------------


STALE = "Hi Alex, I can make it to the community meeting at 5:00 PM today."
NAMED = "Hi Alex, sorry I missed the community meeting on Monday, Nov 23. Can you share the notes?"


def test_the_material_names_the_day_an_old_email_was_sent():
    model = Model(NAMED)
    words, by = asyncio.run(prepare.write_draft(judged(1, **MEETING), "email_reply", model, None,
                                                allowed=set(), now=NOW, tz=TZ))
    assert (words, by) == (NAMED, "model")
    material = model.calls[0][1]
    assert "today is Thursday, Nov 26" in material
    assert "sent on Monday, Nov 23" in material and "\"tomorrow\" means Tuesday, Nov 24" in material


def test_a_draft_saying_today_about_an_old_email_is_written_again():
    model = Model(STALE, NAMED)
    words, by = asyncio.run(prepare.write_draft(judged(1, **MEETING), "email_reply", model, None,
                                                allowed=set(), now=NOW, tz=TZ))
    assert (words, by) == (NAMED, "model") and len(model.calls) == 2


def test_a_draft_that_keeps_saying_today_becomes_the_template():
    words, by = asyncio.run(prepare.write_draft(judged(1, **MEETING), "email_reply", Model(STALE, STALE), None,
                                                allowed=set(), now=NOW, tz=TZ))
    assert by == "template" and "today" not in words.casefold()


def test_today_is_fine_for_an_email_from_today():
    fresh = dict(MEETING, received=NOW - 3600)
    model = Model("Hi Alex, yes, I can make it to the community meeting today.")
    words, by = asyncio.run(prepare.write_draft(judged(1, **fresh), "email_reply", model, None,
                                                allowed=set(), now=NOW, tz=TZ))
    assert by == "model" and "today" in words
    assert "Dates:" not in model.calls[0][1]


def test_a_reused_draft_from_the_day_it_arrived_is_rewritten():
    item = judged(1, **MEETING)
    model = Model(NAMED)
    cards = run([item], model, drafts={(item["primary"], item["asks"][0], "email_reply"): STALE})
    assert cards[0]["action"]["draft"] == NAMED and len(model.calls) == 1


# --- cards kept from an earlier Brief --------------------------------------------------------


def _old(draft, **extra):
    return {"id": "card:a", "title": "Reply to Alex", "account": "home", "todo_id": "todo:a",
            "sources": [{"kind": "mail", "ref": "mail:1"}], "asks": ["ask:mail:1@1"],
            "action": {"method": "email_reply", "draft": draft}, "due_ts": None, **extra}


def _carry(old, tz=TZ):
    item = judged(1, **MEETING)["item"]
    return stable.carry([old], [], items={"mail:1": item}, withheld=set(), gone=set(), suppressed=set(),
                        done_todos=set(), now=NOW, since=NOW - 600, limit=5, tz=tz)


@pytest.fixture
def same_ask(monkeypatch):
    monkeypatch.setattr(stable, "ask_key", lambda item: "ask:mail:1@1")


def test_a_kept_card_whose_meeting_is_over_goes(same_ask):
    assert _carry(_old(NAMED, expires_ts=MONDAY_5PM, due_ts=MONDAY_5PM)) == []


def test_a_kept_card_whose_draft_says_today_about_an_old_email_goes(same_ask):
    assert _carry(_old(STALE)) == []
    assert [c["id"] for c in _carry(_old(NAMED))] == ["card:a"]


def test_without_the_zone_a_kept_card_stays_as_before(same_ask):
    assert [c["id"] for c in _carry(_old(STALE), tz=None)] == ["card:a"]
