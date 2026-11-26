"""A reply sent from Quayside answers its Brief thread at once, whichever path sent it.

The Brief's reply composer (email_reply_api.send_reviewed_reply) and the task
executor already did this bookkeeping. Ask's model-led sends, a text sent from a
Brief card and a text sent from Ask's draft card call the send tools directly, so
the tools themselves mark the thread replied after a VERIFIED send, and nothing
else (an unconfirmed or handed-over send leaves the sweep to read Sent).

Invented people, addresses and messages only; nothing native, nothing live.
"""
import asyncio
import importlib

import pytest

from service.proactive import handled
from service.proactive.store import ProactiveStore
from service.tools import action_tools

NOW = 1_793_000_000.0
MAIL_REF, TEXT_REF = "mail:thread-ox", "messages:chat-58"
HANDLE = "+15550107788"
RECORDS = [(NOW - 900, "chat:58", "Jules: still on for 7?", "g58", True, "iMessage", "+1 (555) 010-7788")]


@pytest.fixture
def brief(tmp_path, monkeypatch):
    s = ProactiveStore(tmp_path / "proactive")
    cards = [
        {"id": "c-mail", "title": "Reply to Rowan", "account": "me@example.com",
         "sources": [{"kind": "mail", "ref": MAIL_REF, "who": "Rowan", "quote": "can you send the agenda?"}],
         "fingerprints": [f"{MAIL_REF}:h1"], "asks": ["ask:agenda"], "action": {"method": "email_reply"}},
        {"id": "c-text", "title": "Text Jules back", "account": "me@example.com",
         "sources": [{"kind": "messages", "ref": TEXT_REF, "who": "Jules", "quote": "still on for 7?"}],
         "fingerprints": [f"{TEXT_REF}:h2"], "asks": ["ask:seven"], "action": {"method": "text_reply"}},
    ]
    s.save_state(as_of=NOW - 600, timezone="America/Los_Angeles", accounts=["me@example.com"], cards=cards,
                 todos=[], threads=[{"id": f"thread:{MAIL_REF}", "needs_reply": True},
                                    {"id": f"thread:{TEXT_REF}", "needs_reply": True}],
                 seen={MAIL_REF: {"source": "mail", "account": "Work", "subject": "Agenda",
                                  "message_id": "<ox1@example.org>", "received_ts": NOW - 3600,
                                  "sender": "rowan@example.org"},
                       TEXT_REF: {"source": "messages", "conversation": "chat:58", "received_ts": NOW - 900}})
    store_mod = importlib.import_module("service.proactive.store")
    monkeypatch.setattr(store_mod, "store", lambda: s)
    monkeypatch.setattr(handled.sources, "text_records", lambda: list(RECORDS))
    hub_mod = importlib.import_module("service.assistant.hub")
    published = []
    monkeypatch.setattr(hub_mod.hub, "publish_sync", lambda event, **_: published.append(event))
    monkeypatch.setattr(action_tools, "_named", lambda handle, typed=None: "Jules", raising=False)
    return type("B", (), {"store": s, "published": published,
                          "cards": lambda self: [c["id"] for c in s.state()["cards"]],
                          "replied": lambda self: {t["id"] for t in s.state()["threads"] if t.get("replied")}})()


def _envelope(to):
    return {"message_id": "<ox1@example.org>", "account": "Work", "account_id": "acct-1",
            "from": "me@example.com", "to": to, "cc": [], "bcc": [],
            "subject": "Re: Agenda", "content": "Here it is, see you Thursday."}


def _reply(envelope, bridge_reply):
    async def bridge(kind, payload, **_):
        assert kind == "reply_to_email"
        return {"ok": True, "accepted": True, "status": "succeeded", "reply": bridge_reply}
    real = action_tools.app_request
    action_tools.app_request = bridge
    try:
        return asyncio.run(action_tools.reply_to_email(
            message_id=envelope["message_id"], body="Here it is, see you Thursday.",
            account="Work", expected_reply=envelope))
    finally:
        action_tools.app_request = real


@pytest.mark.parametrize("to", [["rowan@example.org"],
                                # Mail answered the thread's Reply-To address, not the sender.
                                ["agenda-desk@lists.example.org"]])
def test_asks_verified_email_reply_answers_the_thread_at_once(brief, to):
    env = _envelope(to)
    out = _reply(env, env)
    assert out.startswith("Reply sent.")
    assert brief.cards() == ["c-text"]
    assert f"thread:{MAIL_REF}" in brief.replied()
    assert "ask:agenda" in brief.store.suppressed_fingerprints()
    assert brief.published == [{"type": "changed"}]


def test_an_unconfirmed_email_reply_leaves_the_thread_to_the_sweep(brief):
    env = _envelope(["rowan@example.org"])
    out = _reply(env, {**env, "content": "something else"})
    assert not str(out).startswith("Reply sent.")
    assert brief.cards() == ["c-mail", "c-text"]
    assert brief.published == []


def _text(delivery):
    async def bridge(kind, payload, **_):
        assert kind == "send_message"
        return {"ok": True, "status": "succeeded", **({"delivery": delivery} if delivery else {})}
    real = action_tools.app_request
    action_tools.app_request = bridge
    try:
        with action_tools.human_reviewed_content():
            return asyncio.run(action_tools.send_message(to=HANDLE, text="Yes, see you at 7!"))
    finally:
        action_tools.app_request = real


@pytest.mark.parametrize("delivery", ["sent", "delivered"])
def test_a_verified_text_answers_the_chat_at_once(brief, delivery):
    out = _text(delivery)
    assert out.startswith("Message sent to ")
    assert brief.cards() == ["c-mail"]
    assert f"thread:{TEXT_REF}" in brief.replied()
    assert "ask:seven" in brief.store.suppressed_fingerprints()
    assert brief.published == [{"type": "changed"}]


@pytest.mark.parametrize("delivery", [None, "failed", "uncertain"])
def test_a_text_without_a_verified_send_changes_nothing(brief, delivery):
    out = _text(delivery)
    assert not str(out).startswith("Message sent to ")
    assert brief.cards() == ["c-mail", "c-text"]
    assert brief.published == []


def test_a_brief_text_card_send_answers_its_chat(brief, monkeypatch):
    from service.proactive import executor
    monkeypatch.setattr(executor, "_text_policy_problem", lambda to, draft: None)
    monkeypatch.setattr(executor, "_audit", lambda *a, **k: None)

    async def bridge(kind, payload, **_):
        return {"ok": True, "status": "succeeded", "delivery": "sent"}
    monkeypatch.setattr(action_tools, "app_request", bridge)
    card = {"id": "c-text", "action": {"method": "text_reply", "recipient": HANDLE}}
    result = asyncio.run(executor._text_reply(card, "Yes, see you at 7!"))
    assert result["ok"] is True and result["status"] == "succeeded"
    assert f"thread:{TEXT_REF}" in brief.replied()
    assert brief.cards() == ["c-mail"]


def test_bookkeeping_never_turns_a_sent_text_into_an_error(brief, monkeypatch):
    monkeypatch.setattr(handled, "retire", lambda *a, **k: 1 / 0)
    assert _text("sent").startswith("Message sent to ")


# --- a to-do whose own due day has passed --------------------------------------------------

def test_a_todo_for_an_event_whose_due_day_passed_leaves_even_without_a_date_in_its_title():
    """brief-real-check's sixth false positive: the date lived only in due_ts
    ("Dinner with Sam", due two days ago), so the title-only check kept it."""
    from datetime import datetime
    from service.proactive.cycle import without_passed_events
    now = datetime(2026, 11, 25, 10, 0).timestamp()
    two_days_ago, earlier_today = now - 2 * 86400, now - 3600
    todo = lambda title, source, due: {"title": title, "source": source, "due_ts": due}
    kept = without_passed_events({
        "dinner": todo("Dinner with Sam", "messages", two_days_ago),
        "quiz": todo("Study for the chem quiz", "mail", two_days_ago),
        "note": todo("Bring snacks to the potluck party", "notes", two_days_ago),
        # Late work is still work, and a reply is still owed.
        "late": todo("Finish HW4", "mail", two_days_ago),
        "reply": todo("Reply to Dana about the dinner", "mail", two_days_ago),
        "missed": todo("Ask about the missed quiz", "mail", two_days_ago),
        # The event's own day is still ahead; so is anything with no due time.
        "today": todo("Lunch with Priya", "messages", earlier_today),
        "undated": todo("Dinner with Sam", "messages", None),
        "calendar": todo("Team meeting", "calendar", two_days_ago),
    }, now)
    assert set(kept) == {"late", "reply", "missed", "today", "undated", "calendar"}


# --- "you replied <Day>" names the day the answer went out ---------------------------------

def _replied_store(tmp_path):
    s = ProactiveStore(tmp_path / "proactive-replied")
    ref = "messages:chat-91"
    todo = {"id": "t-form", "title": "Send Dana the signed form", "source": "messages", "account": "me@example.com",
            "kind": "task", "done": False, "due_ts": None, "card_id": None, "fingerprints": [f"{ref}:h1"]}
    s.save_state(as_of=NOW - 600, timezone="America/Los_Angeles", accounts=["me@example.com"], cards=[],
                 todos=[todo], threads=[], seen={ref: {"source": "messages", "conversation": "chat:91",
                                                        "received_ts": NOW - 20 * 86400}})
    return s


def _label(s, now):
    shown = handled.with_replied_labels({"todos": s.state()["todos"]}, store=s, now=now)
    return shown["todos"][0].get("due_label") or ""


def test_a_reply_older_than_the_window_never_reads_as_today(tmp_path):
    """The Auditor's case: a week after the answer its mark is pruned, and the
    next sweep must not mark it again as answered "today"."""
    s = _replied_store(tmp_path)
    replied = NOW - 3 * 86400
    handled.sweep(s, now=NOW, texts={"chat:91": replied}, sent=[], sched=None)
    assert _label(s, NOW) == handled.replied_label(replied)
    later = replied + handled.REPLIED_KEEP_S + 3600
    handled.sweep(s, now=later, texts={"chat:91": replied}, sent=[], sched=None)
    assert _label(s, later) == "", "outside the window: no label, never today's day"
    assert "t-form" not in handled.replied_todos(s, later)
    assert s._read("decisions.json")["replied_todos"]["t-form"] == replied, "never re-marked as now"


def test_a_reply_noticed_late_names_the_day_it_went_out(tmp_path):
    s = _replied_store(tmp_path)
    replied = NOW - 2 * 86400
    handled.sweep(s, now=NOW, texts={"chat:91": replied}, sent=[], sched=None)
    assert handled.replied_todos(s, NOW)["t-form"] == replied
    assert handled.replied_label(replied) != handled.replied_label(NOW)
    assert _label(s, NOW) == handled.replied_label(replied)


def test_a_reply_first_seen_after_the_window_gets_no_label(tmp_path):
    s = _replied_store(tmp_path)
    handled.sweep(s, now=NOW, texts={"chat:91": NOW - handled.REPLIED_KEEP_S - 60}, sent=[], sched=None)
    assert _label(s, NOW) == ""


def test_a_mail_answer_is_dated_by_its_sent_row(tmp_path):
    s = ProactiveStore(tmp_path / "proactive-mail")
    ref = "mail:thread-91"
    s.save_state(as_of=NOW - 600, timezone="America/Los_Angeles", accounts=["me@example.com"], cards=[],
                 todos=[{"id": "t-mail", "title": "Send Rowan the slides", "source": "mail", "account": "me@example.com",
                         "kind": "task", "done": False, "due_ts": None, "card_id": None,
                         "fingerprints": [f"{ref}:h1"]}], threads=[],
                 seen={ref: {"source": "mail", "account": "me@example.com", "subject": "Slides",
                             "message_id": "<s91@example.org>", "received_ts": NOW - 5 * 86400,
                             "sender": "rowan@example.org"}})
    sent = [{"ts": NOW - 4 * 86400, "to": ["rowan@example.org"], "subject": "Re: Slides",
             "in_reply_to": "<s91@example.org>", "account": "me@example.com"},
            {"ts": NOW - 3600, "to": ["someone@example.net"], "subject": "Lunch", "account": "me@example.com"}]
    handled.sweep(s, now=NOW, texts={}, sent=sent, sched=None)
    assert handled.replied_todos(s, NOW)["t-mail"] == NOW - 4 * 86400


# --- Auditor notes on 2162c848 --------------------------------------------------------------

def test_send_bookkeeping_runs_off_the_event_loop_and_publishes_on_it(brief, monkeypatch):
    import threading
    hub_mod = importlib.import_module("service.assistant.hub")
    where = {}
    real = handled.mark_sent_reply

    def tracked(**kw):
        where["work"] = threading.get_ident()
        where["publish"] = kw.get("publish")
        return real(**kw)
    monkeypatch.setattr(handled, "mark_sent_reply", tracked)
    monkeypatch.setattr(hub_mod.hub, "publish_sync",
                        lambda event, **_: where.setdefault("published", (threading.get_ident(), event)))

    async def send():
        where["loop"] = threading.get_ident()
        async def bridge(kind, payload, **_):
            return {"ok": True, "status": "succeeded", "delivery": "sent"}
        monkeypatch.setattr(action_tools, "app_request", bridge)
        with action_tools.human_reviewed_content():
            return await action_tools.send_message(to=HANDLE, text="Yes, see you at 7!")
    assert asyncio.run(send()).startswith("Message sent to ")
    assert where["work"] != where["loop"], "the cache read and state write ran on the event loop"
    assert where["publish"] is False
    assert where["published"] == (where["loop"], {"type": "changed"})
    assert brief.cards() == ["c-mail"]


@pytest.mark.parametrize("tz, kept", [("UTC", False), ("America/Los_Angeles", True)])
def test_an_event_day_is_judged_in_the_briefs_time_zone(tz, kept):
    """Due 2026-11-24 23:30 UTC, now 2026-11-25 00:30 UTC: in UTC the day has
    passed, in Los Angeles it is still the same afternoon, whatever the Mac's zone."""
    from datetime import datetime, timezone
    from service.proactive.cycle import without_passed_events
    due = datetime(2026, 11, 24, 23, 30, tzinfo=timezone.utc).timestamp()
    now = datetime(2026, 11, 25, 0, 30, tzinfo=timezone.utc).timestamp()
    todos = {"dinner": {"title": "Dinner with Sam", "source": "messages", "due_ts": due}}
    assert ("dinner" in without_passed_events(todos, now, tz)) is kept
