"""Brief item accuracy: what the proactive engine reads, which thread an email
belongs to, whether it was answered, and what a to-do says.

Invented people, addresses and messages only; nothing native, nothing live.
"""
import os
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

_scratch = tempfile.TemporaryDirectory(prefix="harbor-brief-items-")
os.environ.setdefault("HARBOR_HOME", _scratch.name)
os.environ.setdefault("MOE_DIR", _scratch.name)

import pytest

from service.proactive import sources, triage
from service.tools import imessage_tools

TZ = "America/Los_Angeles"
NOW = datetime(2026, 11, 25, 10, 0, tzinfo=ZoneInfo(TZ)).timestamp()
ME = "river.example@school.example.edu"
ALIAS = "river.alias@example.org"


@pytest.fixture(autouse=True)
def user_is_river(monkeypatch):
    import service.memory.identity as identity
    monkeypatch.setattr(identity, "user_name", lambda: "River Example")
    monkeypatch.setattr(identity, "user_emails", lambda: [ME, ALIAS])
    monkeypatch.setattr(triage, "user_names", lambda: ["River Example", "River"])


def mail(mid, sender, address, subject, body, ago_h, account=ME, to=ME, **extra):
    return dict(ts=NOW - ago_h * 3600, account=account, account_id="acct-1", sender=sender,
                sender_address=address, to=to, subject=subject, message_id=mid, body=body,
                unread=True) | extra


def items(rows, **kw):
    return sources.mail_items(rows, mine={ME, ALIAS}, now=NOW, tz=TZ, linked=set(), own="me", **kw)


# --- 1. on-device reading keeps ordinary words; only secret values are masked ------------

ORDINARY = [
    "could you confirm Thursday's time for me?",
    "security deposit is due Friday, can you send your half?",
    "Please verify the numbers in the table before the meeting.",
    "what's the password to the wifi at the cabin?",
    "Slides are on www.example.org/talks if you want them.",
    "Can you sign in to the group doc and leave comments?",
]


@pytest.mark.parametrize("body", ORDINARY)
def test_on_device_reading_keeps_ordinary_messages(body):
    assert imessage_tools.redact_secret_values(body) == body


def test_on_device_reading_masks_only_the_values():
    out = imessage_tools.redact_secret_values("Your code is 482913. Call me after.")
    assert "482913" not in out and "Call me after." in out
    out = imessage_tools.redact_secret_values("password: Hunter-2211 and bring snacks")
    assert "Hunter-2211" not in out and "bring snacks" in out
    out = imessage_tools.redact_secret_values("join https://meet.example.com/j/abc?pwd=Zx81kQpLmN0tYv3 at 3")
    assert "Zx81kQpLmN0tYv3" not in out and "meet.example.com" in out and out.endswith(" at 3")
    out = imessage_tools.redact_secret_values("key sk-abcdefghijklmnop1234 is in the vault")
    assert "sk-abcdefghijklmnop1234" not in out
    assert imessage_tools.redact_secret_values("dress code 2026 is formal") == "dress code 2026 is formal"


def test_mail_item_text_keeps_the_ask_and_quotes_stay_strict():
    rows = [mail("<m1@example.org>", "Sam Placeholder", "sam@example.org", "Thursday",
                 "Hi! could you confirm Thursday's time for me?", 2)]
    [item] = items(rows)
    assert "could you confirm Thursday's time for me?" in item["text"]
    # What a card may quote is still the display reading.
    strict = imessage_tools.redact_summary_codes("Hi! could you confirm Thursday's time for me?")
    assert all(line in [item["title"]] + sources._sentences(strict) for line in item["lines"])


def test_text_item_text_keeps_the_ask():
    records = [(NOW - 600, "Dana Placeholder", "Dana Placeholder: security deposit is due Friday, can you send your half?",
                "iMessage;-;+15550000001")]
    [item] = sources.text_items(records, now=NOW, own="me")
    assert "security deposit is due Friday, can you send your half?" in item["text"]


def test_off_this_mac_item_text_stays_strictly_redacted():
    body = "Hi! could you confirm Thursday's time for me?"
    strict = imessage_tools.redact_summary_codes(body)
    assert strict != body, "the strict reading masks this ordinary ask"
    [item] = items([mail("<m1@example.org>", "Sam Placeholder", "sam@example.org", "Thursday", body, 2)],
                   on_device=False)
    assert "could you confirm Thursday's time for me?" not in item["text"]
    records = [(NOW - 600, "Dana Placeholder", "Dana Placeholder: " + body, "iMessage;-;+15550000001")]
    [text] = sources.text_items(records, now=NOW, own="me", on_device=False)
    assert "could you confirm Thursday's time for me?" not in text["text"]


def test_reading_on_device_fails_closed(monkeypatch):
    from types import SimpleNamespace
    import service.config.endpoints as endpoints
    import service.inference.remote_policy as remote_policy
    local = SimpleNamespace(endpoint=SimpleNamespace(managed=True, name="local"))
    monkeypatch.setattr(remote_policy, "remote_only_profile", lambda: None)
    monkeypatch.setattr(endpoints, "role_target", lambda role: local)
    assert sources.reading_on_device() is True
    monkeypatch.setattr(remote_policy, "remote_only_profile", lambda: object())   # remote-only / Everything
    assert sources.reading_on_device() is False
    monkeypatch.setattr(remote_policy, "remote_only_profile", lambda: None)
    monkeypatch.setattr(endpoints, "role_target",
                        lambda role: SimpleNamespace(endpoint=SimpleNamespace(managed=False, name="cloud")))
    assert sources.reading_on_device() is False
    monkeypatch.setattr(endpoints, "role_target", lambda role: 1 / 0)
    assert sources.reading_on_device() is False
    # Under a remote profile, the default build keeps the strict text.
    monkeypatch.setattr(remote_policy, "remote_only_profile", lambda: object())
    body = "Hi! could you confirm Thursday's time for me?"
    [item] = items([mail("<m1@example.org>", "Sam Placeholder", "sam@example.org", "Thursday", body, 2)])
    assert "could you confirm Thursday's time for me?" not in item["text"]


def test_triage_version_bumped_for_the_new_reading():
    assert triage.VERSION != "7"


# --- 2. one subject, different people: different threads --------------------------------

def test_two_senders_under_one_subject_are_two_threads():
    rows = [mail("<sam1@example.org>", "Sam Placeholder", "sam@example.org", "Question",
                 "Can you look at my draft by Friday?", 30),
            mail("<dana1@example.org>", "Dana Placeholder", "dana@example.org", "Question",
                 "Are we still on for lunch?", 20),
            mail("<me1@example.edu>", "River Example", ME, "Re: Question", "Yes, see you at noon.", 10,
                 to="Dana Placeholder <dana@example.org>")]
    found = {i["who"]: i for i in items(rows)}
    assert set(found) == {"Sam Placeholder", "Dana Placeholder"}
    assert found["Dana Placeholder"]["replied"] is True
    assert found["Sam Placeholder"]["replied"] is False, "a reply to Dana never answers Sam"
    assert found["Sam Placeholder"]["id"] != found["Dana Placeholder"]["id"]


def test_one_conversation_stays_one_thread():
    rows = [mail("<sam1@example.org>", "Sam Placeholder", "sam@example.org", "Plans", "Dinner Thursday?", 30),
            mail("<me1@example.edu>", "River Example", ME, "Re: Plans", "Sure!", 20, to="sam@example.org"),
            mail("<sam2@example.org>", "Sam Placeholder", "sam@example.org", "Re: Plans", "Great, 7pm?", 10)]
    [item] = items(rows)
    assert item["replied"] is False and item["who"] == "Sam Placeholder"


def test_subject_match_alone_never_answers_another_sender():
    sent = [dict(ts=NOW - 3600, account=ME, subject="Re: Question", to="dana@example.org",
                 message_id="<me2@example.edu>", in_reply_to="")]
    assert not sources.answered_by_sent(sent, account=ME, subject="Question", message_id="<sam1@example.org>",
                                        after=NOW - 7200, sender="sam@example.org")
    assert sources.answered_by_sent(sent, account=ME, subject="Question", message_id="<dana1@example.org>",
                                    after=NOW - 7200, sender="dana@example.org")


# --- 3. a Gmail web or phone reply clears the thread Mail.app copied ----------------------

def test_reply_header_matches_mail_app_ids_without_brackets_or_case():
    gmail_reply = [dict(ts=NOW - 600, account="river.example@gmail.example", subject="Re: Field trip",
                        to="Kai Placeholder <kai@example.org>", message_id="<r1@mail.gmail.example>",
                        in_reply_to="<ABC123@Example.org>")]
    # Mail.app's copy: no angle brackets, other case, and its account is Mail's own name.
    assert sources.answered_by_sent(gmail_reply, account="Google", subject="Field trip",
                                    message_id="abc123@example.org", after=NOW - 7200)


def test_subject_and_recipient_count_even_when_the_reply_has_its_own_header():
    reply = [dict(ts=NOW - 600, account="river.example@gmail.example", subject="Re: Field trip",
                  to="kai@example.org", message_id="<r2@mail.gmail.example>",
                  in_reply_to="<an-older-message@example.org>")]
    assert sources.answered_by_sent(reply, account="Google", subject="Field trip", message_id="new@example.org",
                                    after=NOW - 7200, sender="kai@example.org")
    assert not sources.answered_by_sent(reply, account="Google", subject="Field trip", message_id="new@example.org",
                                        after=NOW - 7200, sender="someone@else.example")


# --- 4. bulk_mail spares people: Cc'd, [tag] subjects, a quoted newsletter, aliases ----------

def test_cc_only_mail_from_a_person_is_not_list_mail():
    row = mail("<c1@example.org>", "Sam Placeholder", "sam@example.org", "Budget",
               "Can you two check the totals by Monday?", 3, to="ana@example.org, lee@example.org", cc=ME)
    assert not sources.bulk_mail(row)
    # The user in neither To nor Cc, with Cc known: a list or a blind copy.
    assert sources.bulk_mail({**row, "cc": "kim@example.org"})
    # A row from a reader that carries no Cc can't tell: never list mail on To alone.
    assert not sources.bulk_mail({k: v for k, v in row.items() if k != "cc"})


def test_alias_and_send_as_addresses_count_as_the_users(monkeypatch):
    monkeypatch.setattr(sources, "sent_rows", lambda: [dict(ts=NOW - 60, account="river.sendas@example.net")])
    row = mail("<c2@example.org>", "Sam Placeholder", "sam@example.org", "Hi",
               "Lunch Friday?", 3, to="river.sendas@example.net", cc="")
    assert not sources.bulk_mail(row)
    assert not sources.bulk_mail({**row, "to": ALIAS})


@pytest.mark.parametrize("subject", ["[URGENT] need your signature", "Re: [Draft] paper intro"])
def test_a_people_subject_tag_is_not_bulk(subject):
    row = mail("<c3@example.org>", "Sam Placeholder", "sam@example.org", subject, "Please sign today.", 3, cc="")
    assert not sources.bulk_mail(row)


def test_unsubscribe_in_quoted_history_is_not_bulk():
    body = ("Count me in!\n\nOn Tue, Nov 24, 2026 at 9:00 AM Club News <news@example.org> wrote:\n"
            "> Big night Friday.\n> Unsubscribe | Manage preferences")
    row = mail("<c4@example.org>", "Sam Placeholder", "sam@example.org", "Re: Club night", body, 3, cc="")
    assert not sources.bulk_mail(row)
    assert sources.bulk_mail({**row, "body": "Big night Friday.\nUnsubscribe | Manage preferences"})


def test_raw_records_carry_cc_and_old_ones_still_parse():
    from service.tools import email_tools
    fs, rs = "\x01", "\x02"
    new = fs.join(["1795000000", "U", "Google", "acct-1", "Sam <sam@example.org>", "ana@example.org",
                   "Budget", "c5@example.org", "Check the totals?", ME]) + rs
    old = fs.join(["1795000000", "U", "Google", "acct-1", "Sam <sam@example.org>", "ana@example.org",
                   "Budget", "c6@example.org", "Check the totals?"]) + rs
    rows = email_tools._parse_raw_text(new + old)
    assert rows[0]["cc"] == ME and rows[0]["body"] == "Check the totals?" and "cc" not in rows[1]
    assert email_tools._source_identity(new.rstrip(rs), raw=True) == ("Google", "acct-1")


# --- 5. answered email leaves attention for every reader --------------------------------

def test_replied_email_is_not_in_attention():
    from service.assistant import daily_workspace as dw
    day, zone = "2026-11-25", TZ
    state = dict(tasks=[], commitments=[], preferences=dict(start_minute=540, end_minute=1080, not_before=None),
                 sources={}, revision=1)
    common = dict(review_reason="Review this source.", sender="Sam Placeholder", person=True)
    evidence = {"email": dict(ready=True, records=[
        dict(id="email:open", title="Budget", quote="Can you check the totals?", received_ts=NOW - 3600, **common),
        dict(id="email:done", title="Lunch", quote="Lunch Friday?", received_ts=NOW - 7200, replied=True, **common)])}
    out = dw.assess(day, zone, state, evidence, now=NOW)
    titles = [i["title"] for i in out["attention"]]
    assert titles == ["Budget"], titles


# --- 6. caps keep what is still open ------------------------------------------------------

def test_text_cap_keeps_the_unanswered_conversation():
    records = []
    for n in range(sources.MAX_CONVERSATIONS + 5):
        who = f"Friend {n:02d}"
        records += [(NOW - 3600 - n * 60, who, f"{who}: ok see you", f"iMessage;-;+1555000{n:04d}"),
                    (NOW - 3500 - n * 60, who, "Me: sounds good", f"iMessage;-;+1555000{n:04d}")]
    records.append((NOW - 3 * 86400, "Lee Placeholder", "Lee Placeholder: call me back about the lease?",
                    "iMessage;-;+15559990000"))
    kept = sources.text_items(records, now=NOW, own="me")
    assert len(kept) == sources.MAX_CONVERSATIONS
    assert "Lee Placeholder" in {i["who"] for i in kept}, "the one still waiting is never cut"
    assert [i["received_ts"] for i in kept] == sorted((i["received_ts"] for i in kept), reverse=True)


def test_mail_cap_keeps_open_threads_first(monkeypatch):
    monkeypatch.setattr(sources, "MAX_MAIL_THREADS", 2)
    rows = []
    for n in range(3):
        address = f"pal{n}@example.org"
        rows += [mail(f"<p{n}@example.org>", f"Pal {n}", address, f"Topic {n}", "Thoughts?", 5 - n * 0.1),
                 mail(f"<r{n}@example.edu>", "River Example", ME, f"Re: Topic {n}", "Done.", 4 - n * 0.1, to=address)]
    rows.append(mail("<open@example.org>", "Ola Placeholder", "ola@example.org", "Still open", "Can you sign?", 48))
    kept = items(rows)
    assert "Ola Placeholder" in {i["who"] for i in kept}


def test_judge_lines_skip_dismissed_and_put_answered_last():
    from service.proactive import judge

    def survivor(n, replied=False):
        item = dict(id=f"messages:s{n}", hash=f"h{n}", source="messages", who=f"Pal {n}", title=f"Pal {n}",
                    text="hi", received_ts=NOW - n * 60, replied=replied, ref={"conversation": f"Pal {n}"})
        return {"item": item, "result": {"summary": f"Pal {n} said hi.", "asks_of_me": ""}}
    answered = [survivor(n, replied=True) for n in range(judge.MAX_SURVIVOR_LINES)]
    dismissed = survivor(90)
    waiting = survivor(99)
    _, ids = judge.build_input(answered + [dismissed, waiting], {"calendar": [], "reminders": []}, NOW, TZ,
                               suppressed={"messages:s90:h90"})
    shown = [v["survivor"]["item"]["who"] for v in ids.values() if v["kind"] == "item"]
    assert shown[0] == "Pal 99" and "Pal 90" not in shown and len(shown) == judge.MAX_SURVIVOR_LINES


# --- 7. the newest message keeps its ask; forwards and inline replies keep their words -----

def test_long_thread_keeps_the_newest_ask():
    filler = "Here is some background on the venue and the schedule. " * 12
    rows = [mail("<t1@example.org>", "Sam Placeholder", "sam@example.org", "Retreat", filler, 30),
            mail("<t2@example.edu>", "River Example", ME, "Re: Retreat", filler, 20, to="sam@example.org"),
            mail("<t3@example.org>", "Sam Placeholder", "sam@example.org", "Re: Retreat",
                 filler + "\n\nCan you book the van by Thursday?", 10)]
    [item] = items(rows)
    assert len(item["text"]) <= sources.MAX_ITEM_CHARS
    assert "Can you book the van by Thursday?" in item["text"], "the newest message's ask survives the cap"
    assert item["mail_blocks_in"] >= 1


def test_a_long_body_keeps_its_last_paragraph():
    body = "Thanks for the update on the project and the notes. " * 20 + "\n\nWhat time works for you tomorrow?"
    row = mail("<t4@example.org>", "Sam Placeholder", "sam@example.org", "Notes", body, 2)
    assert "What time works for you tomorrow?" in sources._excerpt(row, strict=False)


def test_text_conversation_keeps_its_newest_lines():
    records = [(NOW - 3000 + n, "Kai Placeholder", f"Kai Placeholder: {'long story about the trip ' * 8}{n}",
                "iMessage;-;+15550000002") for n in range(8)]
    records.append((NOW - 100, "Kai Placeholder", "Kai Placeholder: can you call me tonight?", "iMessage;-;+15550000002"))
    [item] = sources.text_items(records, now=NOW, own="me")
    assert len(item["text"]) <= sources.MAX_ITEM_CHARS and "can you call me tonight?" in item["text"]


def test_inline_reply_keeps_the_answers_and_marks_their_time_unknown():
    body = ("Hi,\n> Can you make Friday?\nYes, Friday works.\n> And can you bring the projector?\n"
            "I can bring it, but can you send the room number?")
    text, recovered = sources.own_words(body)
    assert recovered and "Yes, Friday works." in text and "can you send the room number?" in text
    assert "Can you make Friday?" not in text
    rows = [mail("<t5@example.org>", "Sam Placeholder", "sam@example.org", "Re: Friday", body, 2)]
    [item] = items(rows)
    assert "can you send the room number?" in item["text"] and item["body_ts"] is None


def test_forward_keeps_the_forwarded_body():
    body = ("FYI, see below — can you handle this?\n\n---------- Forwarded message ---------\n"
            "From: Office <office@example.org>\nDate: Mon, Nov 23, 2026\nSubject: Parking\n\n"
            "Your parking permit expires on Dec 1. Renew at the front desk.")
    text, recovered = sources.own_words(body)
    assert recovered and "can you handle this?" in text and "[Forwarded] Your parking permit expires on Dec 1." in text
    assert "From: Office" not in text


# --- 8. dates and day halves the sender stated; a counter-offer is no yes ------------------

def _resolve(text):
    from service.attention.resolve import resolve
    return resolve(text, datetime(2026, 11, 25, 10, 0, tzinfo=ZoneInfo(TZ)), TZ)


def test_an_ordinal_day_is_that_date_not_today():
    got = _resolve("party at my place on the 28th at 8pm")
    assert got.start is not None and (got.start.month, got.start.day, got.start.hour) == (11, 28, 20)
    early = _resolve("dentist on the 3rd at 9am")             # the 3rd has passed this month: next month
    assert (early.start.month, early.start.day, early.start.hour) == (12, 3, 9)


@pytest.mark.parametrize("text,hour", [("flight at 6 tomorrow morning", 6), ("gym at 7 tomorrow morning", 7),
                                        ("call at 8 in the morning tomorrow", 8), ("dinner tomorrow at 6", 18),
                                        ("pick up at 5 tomorrow afternoon", 17), ("meet at 9 tomorrow evening", 21)])
def test_the_half_of_the_day_the_sender_named_is_kept(text, hour):
    got = _resolve(text)
    assert got.start is not None and got.start.hour == hour and got.start.day == 26 and not got.guessed


@pytest.mark.parametrize("text", ["meet at 3 tomorrow", "coffee at 9 tomorrow", "breakfast tomorrow at 2",
                                  "lunch or dinner tomorrow at 7"])
def test_a_bare_hour_is_always_a_guess(text):
    got = _resolve(text)
    assert got.start is not None and got.guessed and got.tentative


@pytest.mark.parametrize("reply,confirmed", [
    ("not tomorrow, how about thursday", False), ("who else is coming", False),
    ("who else is coming?", False), ("I'll have to check with my boss first", False),
    ("maybe, let me see", False), ("can't make it", False),
    ("sounds good, see you then", True), ("yes! I'll be there", True), ("great see you then", True)])
def test_only_a_yes_confirms_a_tentative_plan(reply, confirmed):
    from pathlib import Path
    from service.attention import detectors
    from service.attention.corpus import Item, Snapshot
    asked = Item(id="t1", source="messages", conversation="Sam Placeholder", direction="incoming",
                 sender="Sam Placeholder", text="dinner tomorrow at 7?", ts=NOW - 600)
    answer = Item(id="t2", source="messages", conversation="Sam Placeholder", direction="outgoing",
                  sender="Me", text=reply, ts=NOW - 300)
    det = detectors.UncapturedCommitment(Snapshot(root=Path(_scratch.name) / "snap", manifest={}, items=[asked, answer], commitments=[]),
                   tz=TZ)
    assert det._user_confirmed(asked) is confirmed


# --- 9. the user's own tapback answers a chat -------------------------------------------

GUID_KAI = "iMessage;-;+15550000003"


def test_own_tapback_counts_as_answered():
    records = [(NOW - 900, "Kai Placeholder", "Kai Placeholder: made it home, thanks!", GUID_KAI)]
    [open_item] = sources.text_items(records, now=NOW, own="me", acknowledged={})
    assert open_item["replied"] is False
    [liked] = sources.text_items(records, now=NOW, own="me", acknowledged={GUID_KAI: NOW - 600})
    assert liked["replied"] is True and "Liked" not in liked["text"]
    # A reaction from before the newest line answers nothing.
    [older] = sources.text_items(records, now=NOW, own="me", acknowledged={GUID_KAI: NOW - 1200})
    assert older["replied"] is False


def test_handled_sweep_sees_the_tapback():
    from service.proactive import handled
    records = [(NOW - 900, "Kai Placeholder", "Kai Placeholder: made it home, thanks!", GUID_KAI)]
    assert handled.latest_sent_texts(records, acknowledged={GUID_KAI: NOW - 600}) == {"Kai Placeholder": NOW - 600}
    assert handled.latest_sent_texts(records, acknowledged={}) == {}


def test_structured_feed_carries_the_acknowledgements(monkeypatch):
    import json as _json
    coverage = {"version": 1, "kind": "coverage", "attempted": 0, "emitted": 0, "skipped": 0, "truncated": 0,
                "limit": 1, "byte_limit": 1, "window_days": 365, "row_limit": 1, "reached_row_limit": False}
    ack = {"version": 1, "kind": "acknowledged", "chats": [{"conversation": GUID_KAI, "at": NOW - 600}]}
    monkeypatch.setattr(imessage_tools, "messages_sync_state", lambda: "ready")
    monkeypatch.setattr(imessage_tools, "_lines", "V3 | " + _json.dumps(coverage) + "\nV3 | " + _json.dumps(ack))
    snap = imessage_tools.structured_messages_snapshot()
    assert snap["acknowledged"] == {GUID_KAI: NOW - 600} and snap["coverage"]["malformed"] == 0
    assert imessage_tools.acknowledged_chats() == {GUID_KAI: NOW - 600}


# --- fix 10: a verified send answers its thread right away ------------------------

def _sent_store(tmp_path):
    from service.proactive.store import ProactiveStore
    s = ProactiveStore(tmp_path / "proactive")
    now = 1_793_000_000.0
    mail_ref, text_ref = "mail:thread-abc", "messages:chat-42"
    cards = [
        {"id": "c-mail", "title": "Reply to Rowan", "account": "me@example.com",
         "sources": [{"kind": "mail", "ref": mail_ref, "who": "Rowan", "quote": "can you send the slides?"}],
         "fingerprints": [f"{mail_ref}:h1"], "asks": ["ask:slides"], "action": {"method": "email_reply"}},
        {"id": "c-text", "title": "Reply to Jules", "account": "me@example.com",
         "sources": [{"kind": "messages", "ref": text_ref, "who": "Jules", "quote": "dinner at 7?"}],
         "fingerprints": [f"{text_ref}:h2"], "asks": ["ask:dinner"], "action": {"method": "text_reply"}},
    ]
    s.save_state(as_of=now - 600, timezone="America/Los_Angeles", accounts=["me@example.com"], cards=cards,
                 todos=[], threads=[{"id": f"thread:{mail_ref}", "needs_reply": True}], seen={
                     mail_ref: {"source": "mail", "account": "me@example.com", "subject": "Slides",
                                "message_id": "<IN7@Example.org>", "received_ts": now - 3600,
                                "sender": "rowan@example.org"},
                     text_ref: {"source": "messages", "conversation": "chat:42", "received_ts": now - 900}})
    return s, now


def test_verified_mail_send_marks_thread_replied_and_suppresses_its_ask(tmp_path, monkeypatch):
    from service.proactive import handled
    import importlib; hub_mod = importlib.import_module("service.assistant.hub")
    published = []
    monkeypatch.setattr(hub_mod.hub, "publish_sync", lambda event, **_: published.append(event))
    s, now = _sent_store(tmp_path)
    changed = handled.mark_sent_reply(kind="mail", message_id="in7@example.org", account="me@example.com",
                                      subject="Re: Slides", to=["rowan@example.org"], store=s, now=now)
    assert changed["cards"] == ["c-mail"]
    assert changed["threads"] == ["thread:mail:thread-abc"]
    assert [c["id"] for c in s.state()["cards"]] == ["c-text"]
    assert "ask:slides" in s.suppressed_fingerprints()
    assert published == [{"type": "changed"}]


def test_verified_text_send_answers_the_chat_with_that_handle(tmp_path, monkeypatch):
    from service.proactive import handled
    import importlib; hub_mod = importlib.import_module("service.assistant.hub")
    monkeypatch.setattr(hub_mod.hub, "publish_sync", lambda event, **_: None)
    s, now = _sent_store(tmp_path)
    records = [(now - 900, "chat:42", "Jules: dinner at 7?", "g1", True, "iMessage", "+1 (555) 010-4477")]
    changed = handled.mark_sent_reply(kind="text", handle="+15550104477", store=s, now=now, records=records)
    assert changed["cards"] == ["c-text"]
    assert "ask:dinner" in s.suppressed_fingerprints()


def test_sent_reply_bookkeeping_needs_the_right_thread_and_never_raises(tmp_path, monkeypatch):
    from service.proactive import handled
    import importlib; hub_mod = importlib.import_module("service.assistant.hub")
    published = []
    monkeypatch.setattr(hub_mod.hub, "publish_sync", lambda event, **_: published.append(event))
    s, now = _sent_store(tmp_path)
    # Same subject, someone else: not this thread.
    assert handled.mark_sent_reply(kind="mail", account="me@example.com", subject="Re: Slides",
                                   to=["other@example.net"], store=s, now=now)["cards"] == []
    assert published == []
    broken = type("Broken", (), {"state": lambda self: 1 / 0})()
    assert handled.mark_sent_reply(kind="mail", message_id="x", store=broken, now=now) == {
        "cards": [], "todos": [], "threads": [], "replied": []}


# --- fix 11: an answered thread retires its task cards, keeps the to-do ----------

def _task_store(tmp_path):
    from service.proactive.store import ProactiveStore
    s = ProactiveStore(tmp_path / "proactive")
    now = 1_793_000_000.0
    ref = "mail:thread-form"
    cards = [{"id": "c-task", "title": "Send Dana the signed form", "account": "me@example.com",
              "todo_id": "t-form", "sources": [{"kind": "mail", "ref": ref, "who": "Dana", "quote": "the form?"}],
              "fingerprints": [f"{ref}:h9"], "asks": ["ask:form"], "action": {"method": "add_to_day"}},
             {"id": "c-read", "title": "Dana's update on the trip", "account": "me@example.com",
              "sources": [{"kind": "mail", "ref": ref, "who": "Dana", "quote": "fyi"}],
              "fingerprints": [f"{ref}:h8"], "action": {"method": "read"}}]
    todos = [{"id": "t-form", "title": "Send Dana the signed form", "source": "mail", "account": "me@example.com",
              "kind": "task", "done": False, "due_ts": None, "card_id": "c-task", "fingerprints": [f"{ref}:h9"],
              "asks": ["ask:form"]},
             {"id": "t-confirm", "title": "Confirm Saturday with Dana", "source": "mail", "account": "me@example.com",
              "kind": "task", "done": False, "due_ts": None, "card_id": None, "fingerprints": [f"{ref}:h7"]}]
    s.save_state(as_of=now - 600, timezone="America/Los_Angeles", accounts=["me@example.com"], cards=cards,
                 todos=todos, threads=[], seen={})
    return s, now, ref


def test_answered_thread_retires_task_and_read_cards_but_keeps_the_todo_marked(tmp_path):
    from service.proactive import handled
    s, now, ref = _task_store(tmp_path)
    changed = handled.retire(s, answered={ref}, now=now)
    assert sorted(changed["cards"]) == ["c-read", "c-task"]
    assert changed["todos"] == ["t-confirm"], "a confirm is answered by the reply"
    assert changed["replied"] == ["t-form"]
    assert s.state()["cards"] == []
    todo = next(t for t in s.state()["todos"] if t["id"] == "t-form")
    assert todo["replied_label"].startswith("you replied ")
    assert "t-form" not in s.done_todos(now)
    suppressed = s.suppressed_fingerprints()
    assert "ask:form" not in suppressed and f"{ref}:h9" not in suppressed, "the to-do's content stays shown"
    assert "c-task" in s.decisions()["dismissed"]
    # A second sweep changes nothing more.
    assert handled.retire(s, answered={ref}, now=now + 60)["replied"] == []


def test_reply_title_covers_confirm_and_let_know_but_not_send():
    from service.proactive import handled
    for title in ("Confirm the venue", "Let Priya know about Friday", "let them know", "Reply to Ola"):
        assert handled._REPLY_TITLE.match(title), title
    for title in ("Send Dana the signed form", "Share the deck", "Forward the invoice"):
        assert handled._DELIVER_TITLE.match(title), title
    assert not handled._REPLY_TITLE.match("Letter to the editor")


def test_group_chats_are_swept_by_the_users_newest_line():
    from service.proactive import handled
    item = {"source": "messages", "group": True, "received_ts": 1_793_000_000.0,
            "ref": {"kind": "text", "conversation": "chat:77"}}
    entry = handled.seen_entry(item)
    assert entry == {"source": "messages", "conversation": "chat:77", "received_ts": 1_793_000_000.0, "group": True}
    assert handled.answered_refs({"messages:g": entry}, texts={"chat:77": 1_793_000_100.0}, sent=[]) == {"messages:g"}
    assert handled.answered_refs({"messages:g": entry}, texts={"chat:77": 1_792_999_000.0}, sent=[]) == set()


# --- swarm #29: the digits memo follows the cache, not its length --------------

def test_digits_memo_is_rebuilt_for_a_new_cache_of_the_same_length(monkeypatch):
    from service.tools import imessage_tools as M
    monkeypatch.setattr(M.cache_store, "save", lambda *_: None)
    first = "100 | Pat | Pat: call me at 5550104477"
    second = "100 | Pat | Pat: call me at 5550109988"
    assert len(first) == len(second)
    M.cache_messages(first, available=True)
    assert "5550104477" in M._messages_digits()
    M.cache_messages(second, available=True)
    assert "5550109988" in M._messages_digits() and "5550104477" not in M._messages_digits()
    # Even a cache swapped in place (no sync) is read again.
    monkeypatch.setattr(M, "_lines", first)
    assert "5550104477" in M._messages_digits()


# --- swarm #28: a "|" in a chat name stays inside its field ------------------------

def test_v2_line_with_escaped_pipe_in_the_chat_name_keeps_the_body_whole(monkeypatch):
    from service.tools import imessage_tools as M
    line = "V2 | 1793000000.0 | U | chat:4 | Team ¦ Ops | +15555550144: Standup moved | see thread"
    rows = M._parse_records_uncached(line)
    assert len(rows) == 1
    ts, conversation, context, text, unread = rows[0]
    assert (conversation, unread) == ("chat:4", True)
    assert context == "Team ¦ Ops"
    assert text.endswith("Standup moved | see thread")
