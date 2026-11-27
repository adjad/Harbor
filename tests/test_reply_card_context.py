"""A reply card says what it answers: the subject and the line the message is
about, by code from the message, masked like a draft's material. Every name,
address and message here is invented."""
from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from service.proactive import draft_context, prepare

TZ = "America/Chicago"
NOW = datetime(2026, 11, 26, 10, 0, tzinfo=ZoneInfo(TZ)).timestamp()      # a Thursday
DAY = 86400


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(prepare, "_user_names", lambda: [])
    monkeypatch.setattr(draft_context, "on_device", lambda: False)


def judged(*, source="mail", subject="Community meeting", lines=None, quote="", received=NOW - 3600,
           pending=None, **raw_extra):
    raw = {"id": "mail:1", "source": source, "person": True, "title": subject, "text": "x",
           "lines": [subject] + list(lines or []) if source == "mail" else list(lines or []),
           "received_ts": received, "sender_address": "alex@example.org", "ref": {},
           "reply_ref": {"message_id": "m1@example.org", "account": "Home", "account_id": ""}, **raw_extra}
    if pending is not None:
        raw["pending"] = pending
    return {"item": raw, "who": "Alex Rivera", "title": "Reply to Alex", "source": source, "kind": "reply",
            "primary": "mail:1", "account": "home", "due_ts": None, "has_clock": False, "quote": quote,
            "asks": ["ask:mail:1@1"], "fingerprints": ["mail:1:1"], "urgency": "high", "why": ""}


def context(item):
    return prepare.reply_context(item, NOW, TZ)


def test_the_subject_and_the_line_it_is_about():
    item = judged(lines=["Hi Robin!", "Can you come to the community meeting Thursday at 5?"],
                  quote="Can you come to the community meeting Thursday at 5?")
    assert context(item) == "Community meeting — “Can you come to the community meeting Thursday at 5?”"


def test_greetings_are_skipped_for_the_first_line_that_says_something():
    item = judged(subject="Re: Weekend plans", lines=["Hey there!", "Hope you're doing well.",
                                                      "Are you free to help move the couch on Saturday?"])
    assert context(item) == "Weekend plans — “Are you free to help move the couch on Saturday?”"


def test_addresses_and_links_are_masked():
    item = judged(lines=["Send it to sam@example.net or see https://example.com/x please."])
    shown = context(item)
    assert "sam@example.net" not in shown and "example.com" not in shown
    assert "(email address)" in shown and "(link)" in shown


def test_a_message_holding_a_code_shows_only_its_subject():
    shown = context(judged(lines=["Your code is 482913, use it to sign in.", "Can you come Thursday?"]))
    assert shown == "Community meeting"


def test_a_label_and_its_value_on_two_lines_are_caught():
    item = judged(lines=["Here is the wifi password:", "Tq9mzP4vRx77", "Can you come Thursday?"])
    assert "Tq9mzP4vRx77" not in context(item)
    assert context(item) == "Community meeting"


def test_a_sensitive_reading_shows_no_words():
    item = judged(lines=["Can you come to the community meeting Thursday at 5?"])
    item["result"] = {"sensitive": True}
    assert context(item) == ""


def test_a_message_the_source_check_withholds_shows_no_words_without_a_secret_flag():
    item = judged(lines=["Can you come to the community meeting Thursday at 5?"],
                  text="Subject: Community meeting\nMy card is 4111 1111 1111 1111, can you book it?")
    assert "secret" not in item["item"]
    assert context(item) == ""


def test_a_withheld_or_secret_message_shows_no_words():
    assert context(judged(subject="Private details omitted.", lines=["Private details omitted."])) == ""
    assert context(judged(lines=["Can you come Thursday?"], secret=True)) == ""
    assert context(judged(lines=["Can you come Thursday?"], critical=True)) == ""


def test_a_text_uses_what_they_wrote_since_the_users_last_line():
    item = judged(source="messages", subject="Alex", lines=["ok", "Want to grab lunch tomorrow at noon?"],
                  pending=["Want to grab lunch tomorrow at noon?"])
    assert context(item) == "“Want to grab lunch tomorrow at noon?”"


def test_an_old_messages_today_says_the_day_it_was_sent():
    item = judged(lines=["The community meeting is at 5 PM today, can you make it?"],
                  received=datetime(2026, 11, 23, 9, 0, tzinfo=ZoneInfo(TZ)).timestamp())
    assert context(item) == "Community meeting — Mon: “The community meeting is at 5 PM today, can you make it?”"


def test_a_long_line_is_cut_at_a_word():
    long = "Could you look over " + "the plan for the garden and the budget " * 10 + "before Friday?"
    shown = context(judged(lines=[long]))
    gist = shown.split("“", 1)[1].removesuffix("…”")
    assert shown.endswith("…”") and len(gist) <= prepare.GIST_CHARS
    assert long.startswith(gist) and long[len(gist)] == " "          # cut between words


def test_the_subject_alone_when_no_line_says_anything():
    assert context(judged(lines=["Thanks!"])) == "Community meeting"


def test_a_reply_card_carries_its_context_and_other_cards_do_not():
    def freeze(ref):
        return {"ok": True, "can_send": True, "envelope": "sealed", "to": ["alex@example.org"], "subject": "Re: x"}

    async def model(system, material, abort, tokens):
        return ""
    item = judged(lines=["Can you come to the community meeting Thursday at 5?"])
    _state, cards = asyncio.run(prepare.prepare([item], {"calendar": [], "reminders": []}, model, None,
                                                now=NOW, tz=TZ, freeze=freeze))
    assert cards[0]["action"]["method"] == "email_reply"
    assert cards[0]["context"].startswith("Community meeting — “Can you come")
    errand = dict(judged(), kind="errand", due_ts=NOW + 2 * DAY, title="Pick up the paint")
    errand["item"] = dict(errand["item"], reply_ref=None)
    _state, cards = asyncio.run(prepare.prepare([errand], {"calendar": [], "reminders": []}, model, None,
                                                now=NOW, tz=TZ, freeze=freeze))
    assert cards and "context" not in cards[0]
