"""Reply drafts written from the thread: the longer material, grounding, the retry,
template drafts, and the thread-row email draft. Every name and message is invented."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from service.assistant import email_reply_api as api
from service.proactive import draft_context, prepare, sources, stable

TZ = "America/Los_Angeles"
NOW = datetime(2026, 11, 23, 10, 0, tzinfo=ZoneInfo(TZ)).timestamp()      # a Monday
HOUR = 3600
ME = "robin@example.edu"


def row(mid, sender, body, *, ago, subject="Poster session", to=ME, account="School"):
    return {"message_id": mid, "account": account, "account_id": "", "sender": sender, "to": to,
            "subject": subject, "ts": NOW - ago, "body": body}


NEWEST = row("m3@example.edu", "Dana Reyes <dana@example.edu>",
             "Hi Robin, could you confirm the poster size and tell me whether Thursday at 2 works "
             "for the dry run? We have the room until 4.", ago=HOUR)
FIRST = row("m1@example.edu", "Dana Reyes <dana@example.edu>",
            "The session is in Kerr Hall. Posters can be 36 by 48 inches.", ago=50 * HOUR)
MINE = row("m2@example.edu", f"Robin Vale <{ME}>", "Great, I'll bring the Figma printout.", ago=40 * HOUR,
           to="dana@example.edu")
OTHER = row("x1@example.net", "Sam Ito <sam@example.net>", "Unrelated: my locker is 12.", ago=30 * HOUR)


@pytest.fixture
def mail(monkeypatch):
    rows = [dict(NEWEST), dict(FIRST), dict(MINE), dict(OTHER)]
    monkeypatch.setattr(api, "_mail_rows", lambda: [dict(r) for r in rows])
    monkeypatch.setattr(api, "_header_rows", lambda: [])
    monkeypatch.setattr(draft_context, "my_addresses", lambda: {ME})
    monkeypatch.setattr(draft_context, "on_device", lambda: True)
    monkeypatch.setattr(draft_context, "_calendar_events", lambda start, end: [])
    monkeypatch.setattr(draft_context, "_index_cache", (time.monotonic(), []))
    return rows


def judged(raw=None, **extra):
    raw = raw or {"source": "mail", "text": "Subject: Poster session\nFrom Dana: could you confirm the size?",
                  "reply_ref": {"message_id": NEWEST["message_id"], "account": "School", "account_id": ""}}
    return {"item": raw, "who": "Dana Reyes", "title": "Reply to Dana about the poster", "source": raw["source"],
            "result": {"summary": "Dana asks about the dry run.", "asks_of_me": "Confirm the poster size"}, **extra}


class Model:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    async def __call__(self, system, material, abort, tokens):
        self.calls.append((system, material, tuple(tokens)))
        return self.replies.pop(0) if self.replies else ""


def write(item, model, method="email_reply"):
    return asyncio.run(prepare.write_draft(item, method, model, None, allowed=set(), now=NOW, tz=TZ))


# --- the material ---------------------------------------------------------------------


def test_the_material_holds_the_thread_the_users_reply_and_nobody_else(mail):
    material, source = asyncio.run(draft_context.build(judged(), now=NOW, tz=TZ, budget=6000))
    assert "could you confirm the poster size" in material           # ordinary words stay
    assert "36 by 48 inches" in material and "From You (the user)" in material
    assert material.index("36 by 48") < material.index("Figma") < material.index("THE MESSAGE TO ANSWER")
    assert "locker" not in material                                   # another person's same subject
    assert "Thursday, Nov 26: nothing on the calendar" in material
    assert "36 by 48" in source


def test_a_tight_budget_drops_the_oldest_history_first():
    material, _ = draft_context.assemble("N" * 600, ["OLDEST " + "a" * 500, "NEWER " + "b" * 500], "", 1400)
    assert "NEWER" in material and "OLDEST" not in material and "N" * 600 in material


def test_a_long_message_is_cut_at_a_sentence_and_keeps_its_last_question():
    text = ("This is one full sentence about the plan. " * 120) + "\n\nCan you send the slides by Friday?"
    cut = draft_context.cut_at_sentence(text, draft_context.NEWEST_CHARS)
    assert len(cut) <= draft_context.NEWEST_CHARS
    assert cut.endswith("Can you send the slides by Friday?") and "plan. …" in cut


@pytest.mark.parametrize("text", ["could you confirm Thursday's time for me?", "Please verify the numbers in the table.",
                                  "The security deposit is due Friday, can you send your half?",
                                  "the code review is done", "course code CS101 meets Tuesday",
                                  "what's the password to the wifi"])
def test_ordinary_words_are_not_secrets(text):
    assert draft_context.safe_text(text) == text


@pytest.mark.parametrize("text,value", [("Your login code is 482913. Can you come Friday?", "482913"),
                                        ("password: hunter2x and can you come Friday?", "hunter2x"),
                                        ("the wifi password is bluebird, can you come Friday?", "bluebird"),
                                        ("key sk-abcdefghij1234567890, can you come Friday?", "abcdefghij"),
                                        ("card 4111 1111 1111 1111. Can you come Friday?", "1111")])
def test_a_secret_value_is_masked_and_the_ask_stays(text, value):
    safe = draft_context.safe_text(text)
    assert value not in safe and "come Friday?" in safe and "[" not in safe


@pytest.mark.parametrize("text", ["Your code:\n7H3K9Q", "Your login code 7H3K9Q works until noon."])
def test_a_message_still_holding_a_secret_after_masking_is_left_out(text):
    assert draft_context.safe_text(text) == ""


def test_links_addresses_and_numbers_are_taken_out():
    text = draft_context.safe_text("Call +1 (555) 010-4477 or write kim@example.org, notes at https://example.org/a?k=1")
    assert "555" not in text and "@" not in text and "http" not in text


def test_inline_answers_and_forwards_are_not_emptied():
    inline = "> Are you free Friday?\nYes, after lunch.\n> Which room?\nThe small one."
    assert draft_context.own_words(inline) == "Yes, after lunch.\nThe small one."
    forward = "See below.\n\n---------- Forwarded message ---------\nThe deadline moved to Friday."
    assert "deadline moved" in draft_context.own_words(forward)
    quoted = "Sounds good to me, Thursday it is then, see you there.\n> old words"
    assert "old words" not in draft_context.own_words(quoted)


def test_free_busy_gives_times_and_never_titles():
    start = datetime(2026, 11, 26, 14, 0, tzinfo=ZoneInfo(TZ)).timestamp()
    days = draft_context.named_days("Does Thursday work?", NOW - HOUR, NOW, TZ)
    line = draft_context.free_busy(days, TZ, lambda a, b: [{"when_ts": start, "end_ts": start + HOUR,
                                                           "title": "Therapy", "all_day": False}])
    assert line == "Thursday, Nov 26: busy 2:00 PM to 3:00 PM"
    assert draft_context.free_busy(days, TZ, lambda a, b: None) == ""        # unread is not free


def test_mails_own_conversation_adds_sent_replies_and_a_missing_body(mail, monkeypatch):
    mail[:] = [dict(NEWEST, body="")]
    index = [{"rowid": 1, "rfc_message_id": "<m2@example.edu>", "message_id": "", "conversation_id": 9,
              "box": "sent", "ts": NOW - 40 * HOUR},
             {"rowid": 2, "rfc_message_id": "<m3@example.edu>", "message_id": "", "conversation_id": 9,
              "box": "inbox", "ts": NOW - HOUR}]
    monkeypatch.setattr(draft_context, "_index_cache", (time.monotonic(), index))

    async def body(rowid):
        from types import SimpleNamespace
        if rowid == 4:                                      # grouped by subject: someone else's
            return SimpleNamespace(sender=OTHER["sender"], to=ME, subject="Poster session", body=OTHER["body"])
        return SimpleNamespace(sender=f"Robin Vale <{ME}>" if rowid == 1 else NEWEST["sender"],
                               to="dana@example.edu" if rowid == 1 else ME, subject="Poster session",
                               body="I can bring tape." if rowid == 1 else "Can you confirm the size?")
    index.append({"rowid": 4, "rfc_message_id": "<x9@example.net>", "message_id": "", "conversation_id": 9,
                  "box": "inbox", "ts": NOW - 20 * HOUR})
    monkeypatch.setattr(draft_context, "_body", body)
    found, earlier = asyncio.run(draft_context.mail_thread(dict(NEWEST, body=""), [dict(NEWEST, body="")],
                                                           local=True))
    assert found["body"] == "Can you confirm the size?"
    assert [(m["mine"], m["body"]) for m in earlier] == [(True, "I can bring tape.")]


def test_texts_split_the_waiting_lines_from_the_history(mail, monkeypatch):
    rows = [(NOW - 5 * HOUR, "Priya: want to study Saturday?"), (NOW - 4 * HOUR, "Me: yes, library"),
            (NOW - HOUR, "Priya: can you bring the notes?")]
    monkeypatch.setattr(draft_context, "_text_rows", lambda conversation: rows)
    raw = {"source": "messages", "ref": {"conversation": "Priya"}, "text": "Priya: can you bring the notes?"}
    material, _ = asyncio.run(draft_context.build(judged(raw), now=NOW, tz=TZ, budget=6000))
    answer = material.split("THE MESSAGE TO ANSWER:")[1]
    assert "bring the notes" in answer and "library" not in answer and "yes, library" in material


# --- writing the draft ---------------------------------------------------------------


GOOD = ("Hi Dana, posters can be 36 by 48 inches, as you wrote. Thursday at 2 looks open for me, so I'll "
        "plan on the dry run. I'll bring the Figma printout to Kerr Hall.")


def test_a_draft_is_held_to_the_whole_thread(mail):
    model = Model(GOOD)
    assert write(judged(), model) == (GOOD, "model")
    assert len(model.calls) == 1 and model.calls[0][2] == prepare.DRAFT_TOKENS
    system, material, _ = model.calls[0]
    assert "2 to 5 sentences" in system and "gentle" in system
    assert "What they ask of you: Confirm the poster size" in material


def test_one_retry_then_the_template(mail):
    model = Model("Sure, see you in Boston at 9.", GOOD)
    assert write(judged(), model) == (GOOD, "model")
    assert len(model.calls) == 2 and prepare.RETRY_RULE in model.calls[1][0]
    model = Model("See you in Boston.", "See you in Boston.", GOOD)
    words, source = write(judged(), model)
    assert source == "template" and prepare.is_template(words) and len(model.calls) == 2


def test_no_model_means_one_call_and_a_template(mail):
    model = Model("")
    assert write(judged(), model)[1] == "template" and len(model.calls) == 1


def test_the_notes_never_vouch_for_a_name(mail):
    item = judged(result={"summary": "Dana mentions Zurich.", "asks_of_me": ""})
    assert write(item, Model("See you in Zurich.", "See you in Zurich."))[1] == "template"


@pytest.mark.parametrize("bad", ["Hi Dana, details at example.org.", "Hi Dana, write me at robin@example.edu.",
                                 "Hi Dana, " + "yes " * 400])
def test_addresses_links_and_overlong_drafts_are_refused(mail, bad):
    assert prepare.valid_draft(bad, judged(), set()) == ""


def test_a_model_elsewhere_gets_only_the_short_text(mail, monkeypatch):
    monkeypatch.setattr(draft_context, "on_device", lambda: False)

    async def never(*a, **k):
        raise AssertionError("no longer material off this Mac")
    monkeypatch.setattr(draft_context, "build", never)
    model = Model("Hi Dana, I'll confirm soon.")
    write(judged(), model)
    material = model.calls[0][1]
    assert "Figma" not in material and "Notes from an earlier reading" not in material
    assert "could you confirm the size?" in material


@pytest.mark.parametrize("answer", [True, False])
def test_drafts_and_item_text_share_one_on_device_check(monkeypatch, answer):
    monkeypatch.setattr(sources, "reading_on_device", lambda: answer)
    assert draft_context.on_device() is answer


# --- templates are never carried forward ----------------------------------------------


def _card(draft, source=None):
    action = {"method": "email_reply", "draft": draft, **({"draft_source": source} if source else {})}
    return {"action": action, "asks": ["ask:a@1"], "sources": [{"ref": "a"}]}


def test_template_drafts_are_not_reused():
    item = judged()
    old = prepare.template_draft(item, "email_reply")
    assert stable.reusable_drafts([_card(old, "template")]) == {}
    assert stable.reusable_drafts([_card(old)]) == {}                       # saved before the mark existed
    assert stable.reusable_drafts([_card(prepare.template_draft(item, "text_reply"))]) == {}
    assert list(stable.reusable_drafts([_card(GOOD, "model")]).values()) == [GOOD]


def test_a_card_records_where_its_draft_came_from_and_is_rewritten(mail, monkeypatch):
    monkeypatch.setattr(prepare, "_user_names", lambda: [])
    raw = {"source": "mail", "person": True, "reply_ref": judged()["item"]["reply_ref"], "text": "poster",
           "ref": {}, "received_ts": NOW - HOUR}
    item = judged(raw, kind="reply", primary="mail:a", account="school", due_ts=None, asks=["ask:mail:a@1"],
                  fingerprints=["mail:a:1"], urgency="high", why="")
    freeze = lambda ref: {"ok": True, "can_send": True, "envelope": "e", "to": ["dana@example.edu"], "subject": "Re: x"}
    sched = {"calendar": [], "reminders": []}

    def cards(model, drafts=None):
        return asyncio.run(prepare.prepare([dict(item)], sched, model, None, now=NOW, tz=TZ, freeze=freeze,
                                           drafts=drafts))[1]
    first = cards(Model(""))
    assert first[0]["action"]["draft_source"] == "template"
    again = cards(Model(GOOD), stable.reusable_drafts(first))
    assert again[0]["action"]["draft"] == GOOD and again[0]["action"]["draft_source"] == "model"


# --- the email composer's own draft ---------------------------------------------------


def test_the_composer_draft_sees_the_users_reply_and_the_calendar(mail, monkeypatch):
    prompts = []

    async def model(system, material):
        prompts.append((system, material))
        return "Hi Dana, Thursday at 2 looks open."
    monkeypatch.setattr(api, "_model_reply", model)
    monkeypatch.setattr(api, "_native_accounts", lambda: [])
    out = asyncio.run(api.draft_reply({"message_id": NEWEST["message_id"], "account": "School", "account_id": ""}))
    system, material = prompts[0]
    assert out["draft"] == "Hi Dana, Thursday at 2 looks open."
    assert "Two to five plain sentences" in system
    assert "the user wrote this earlier" in material and "Figma" in material and "locker" not in material


def test_the_composer_off_this_mac_keeps_sent_mail_home(mail, monkeypatch):
    monkeypatch.setattr(draft_context, "on_device", lambda: False)
    _row, earlier, calendar = asyncio.run(api._thread_for_draft(dict(NEWEST)))
    assert [m["message_id"] for m in earlier] == [FIRST["message_id"]] and calendar == ""


def test_the_composer_refuses_a_draft_with_an_address():
    assert api.clean_draft("Write to kim@example.org.") == ""
