"""Ask reads one merged day: Day's planned blocks and due items beside the calendar.

Real failure: "text Kristy my day today" drafted only calendar events and Reminders and missed
Day's own blocks (a 9:00 reading block, a 10:30 homework block, a noon email block). The Day tab
and the calendar are one schedule, so get_upcoming (and daily_brief, which reads it) and
get_my_day_plan now include Day's blocks, in time order with the calendar's events, each item once,
whether or not the calendar sync is on.

Invented data only; no model, no server, no user store.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from service.assistant import day_agenda
from service.proactive import cycle
from service.tools import assistant_tools, proactive_tools, timeranges

TZ = "America/Los_Angeles"
NOW = datetime(2026, 11, 2, 18, 20)          # Mon, local (TZ below)


def ts(day, hour=0, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=ZoneInfo(TZ)).timestamp()


def cal(title, day, hour, minute=0, *, id_, minutes=30, **more):
    return {"id": id_, "title": title, "source": "calendar", "kind": "meeting", "when_ts": ts(day, hour, minute),
            "end_ts": ts(day, hour, minute) + minutes * 60, "all_day": False, "account": "", "location": "",
            "organizer": "", "source_id": "native-" + id_, **more}


# Tomorrow, Tue Nov 3: Day's plan holds three blocks the calendar does not.
BLOCKS = [
    {"id": "study-block:r1", "title": "Reading: Chapter 4, STAT 101", "start": ts(3, 9), "end": ts(3, 10),
     "kind": "study", "task_id": None, "warnings": [], "added": False, "event_source_id": None},
    {"id": "task:t1", "title": "Homework 3", "start": ts(3, 10, 30), "end": ts(3, 11, 30), "kind": "task",
     "task_id": "t1", "warnings": []},
    {"id": "daily:e1", "title": "Review: 3 emails", "start": ts(3, 12), "end": ts(3, 12, 30), "kind": "suggestion",
     "task_id": None, "warnings": [], "evidence_id": "mail:1"},
    # Never rows: the person's own calendar events, and time Harbor only guesses is busy.
    {"id": "cal:9", "title": "Team sync", "start": ts(3, 14), "end": ts(3, 14, 30), "kind": "calendar",
     "task_id": None, "warnings": []},
    {"id": "behavior:p", "title": "Likely busy", "start": ts(3, 16), "end": ts(3, 17), "kind": "behavior",
     "task_id": None, "warnings": []},
]
CALENDAR = [cal("Team sync", 3, 14, id_="c1"), cal("Office hours", 3, 11, 45, id_="c2")]
DEADLINES = [{"id": "d1", "title": "Lab report due", "due_ts": ts(3, 23, 59), "source": "study"}]


def plan_for(day, _tz):
    if day != "2026-11-03":
        return {"blocks": [], "deadlines": []}
    return {"blocks": BLOCKS, "deadlines": DEADLINES, "workspace": {"proposed_plan": {"blocks": BLOCKS}}}


@pytest.fixture(autouse=True)
def world(monkeypatch):
    monkeypatch.setenv("TZ", TZ)
    time.tzset()
    monkeypatch.setenv("HARBOR_SCHEDULE_BRIEF_ITEMS", "1")
    clock = SimpleNamespace(time=lambda: NOW.timestamp())
    monkeypatch.setattr(assistant_tools, "time", clock)
    monkeypatch.setattr(proactive_tools, "time", clock)
    rows = {"calendar": list(CALENDAR), "reminders": []}
    fake_store = SimpleNamespace(
        upcoming=lambda **_kw: [], hidden_commitment_ids=lambda *_a: set(),
        active_between=lambda start, end: [r for r in rows["calendar"] + rows["reminders"]
                                           if start <= r["when_ts"] < end],
        calendar_events_overlapping=lambda start, end: [
            r for r in rows["calendar"] if r["when_ts"] < end and r["end_ts"] > start])
    monkeypatch.setattr(assistant_tools, "assistant_store", fake_store)
    monkeypatch.setattr("service.assistant.store.assistant_store", fake_store)
    monkeypatch.setattr(timeranges, "resolve_span",
                        lambda period, **kw: timeranges.resolve_period(period, **{**kw, "now": NOW}))
    monkeypatch.setattr("service.assistant.sync_status.ensure_sources", AsyncMock(return_value={"sources": [
        {"id": "calendar", "label": "Calendar", "state": "ready"},
        {"id": "reminders", "label": "Reminders", "state": "ready"}]}))
    monkeypatch.setattr("service.proactive.store.store", lambda: (_ for _ in ()).throw(RuntimeError("no store")))
    monkeypatch.setattr(cycle, "_zone", lambda: TZ)
    monkeypatch.setattr(day_agenda, "snapshot_fn", plan_for)
    monkeypatch.setattr(cycle, "snapshot", lambda **kw: {"state": "ready", "todos": [
        {"id": "x1", "title": "Team sync", "source": "calendar", "due_ts": ts(3, 14), "due_label": "", "done": False,
         "timed": True}] + ([] if kw.get("schedule_fn") is None else []), "threads": [], "cards": []})
    monkeypatch.setattr("service.proactive.sources.schedule", lambda *_a, **_k: {"calendar": [], "reminders": []})
    world.rows = rows
    yield
    monkeypatch.undo()
    time.tzset()


def upcoming(**kw):
    return asyncio.run(assistant_tools.get_upcoming(**kw))


def order(text, *needles):
    positions = [text.index(n) for n in needles]
    assert positions == sorted(positions), (needles, text)


# --- get_upcoming ("my day tomorrow", agenda) ----------------------------------------------

def test_my_day_tomorrow_includes_days_blocks_in_time_order_with_the_calendar():
    text = upcoming(period="tomorrow")
    for title in ("Reading: Chapter 4, STAT 101", "Homework 3", "Office hours", "Review: 3 emails", "Team sync"):
        assert text.count(title) == 1, (title, text)
    order(text, "Reading: Chapter 4", "Homework 3", "Office hours", "Review: 3 emails", "Team sync")
    assert "Likely busy" not in text
    assert "9:00 AM" in text and "10:30 AM" in text and "12:00 PM" in text


def test_a_suggestion_is_labelled_and_a_kept_block_is_not():
    text = upcoming(period="tomorrow")
    line = lambda title: next(l for l in text.splitlines() if title in l)
    assert "suggested time, not kept yet" in line("Review: 3 emails")
    assert "planned by Harbor" in line("Homework 3") and "until 11:30 AM" in line("Homework 3")
    assert "planned by Harbor" in line("Reading: Chapter 4")


def test_what_is_due_that_day_is_listed_once():
    text = upcoming(period="tomorrow")
    assert text.count("Lab report due") == 1 and "due" in next(l for l in text.splitlines() if "Lab report due" in l)
    # A due item a Reminders row already lists is not listed again.
    world.rows["reminders"].append({"id": "r9", "title": "Lab report due", "source": "reminders", "kind": "reminder",
                                   "when_ts": ts(3, 23, 59), "end_ts": None, "all_day": False, "account": ""})
    assert upcoming(period="tomorrow").count("Lab report due") == 1


def test_a_block_already_on_the_calendar_counts_once():
    # The older "Add to calendar" copy: same title and start, and the event id the study flow recorded.
    world.rows["calendar"] += [cal("Homework 3", 3, 10, 30, id_="c3"),
                               cal("Reading: Chapter 4 (study)", 3, 9, id_="c4")]
    text = upcoming(period="tomorrow")
    assert text.count("Homework 3") == 1
    study = dict(BLOCKS[0], event_source_id="native-c4", added=True)
    day_agenda.snapshot_fn = lambda day, tz: {"blocks": [study, *BLOCKS[1:]], "deadlines": []}
    text = upcoming(period="tomorrow")
    assert "Reading: Chapter 4 (study)" not in text and text.count("Reading: Chapter 4, STAT 101") == 1


def test_it_works_with_the_calendar_sync_off_or_on_and_never_needs_the_calendar_copy():
    # Sync on: the written event is marked, so it never reaches the calendar rows at all.
    assert "Homework 3" in upcoming(period="tomorrow")
    world.rows["calendar"][:] = []
    text = upcoming(period="tomorrow")
    assert "Homework 3" in text and "Reading: Chapter 4" in text and "Nothing" not in text.split("\n")[0]


def test_daily_brief_the_read_behind_a_day_summary_to_send_carries_the_blocks(monkeypatch):
    from service.tools import everyday

    async def calendar_read(args, now, start, end):          # the receipt check needs a real store
        return await assistant_tools.get_upcoming(**args)
    monkeypatch.setattr(everyday, "_daily_calendar", calendar_read)
    monkeypatch.setattr(everyday, "time", SimpleNamespace(time=lambda: NOW.timestamp()))
    text = asyncio.run(everyday.daily_brief(period="tomorrow", include_activity=False, include_reminders=False,
                                            include_weather=False))
    for title in ("Reading: Chapter 4, STAT 101", "Homework 3", "Office hours"):
        assert text.count(title) == 1, (title, text)
    order(text, "Reading: Chapter 4", "Homework 3", "Office hours")


def test_today_and_other_days_do_not_borrow_tomorrows_blocks():
    assert "Homework 3" not in upcoming(period="today")
    assert "Homework 3" not in upcoming(period="friday")


def test_an_account_filter_or_end_time_question_is_about_the_calendar_only():
    assert "Homework 3" not in upcoming(period="tomorrow", account="Work")
    assert "Homework 3" not in upcoming(period="tomorrow", calendar_only=True, query="Office hours", boundary="end")


def test_a_day_plan_that_cannot_be_read_costs_nothing(monkeypatch):
    def boom(*_a):
        raise RuntimeError("no plan")
    monkeypatch.setattr(day_agenda, "snapshot_fn", boom)
    text = upcoming(period="tomorrow")
    assert "Office hours" in text and "Homework 3" not in text


def test_a_query_scopes_day_rows_too():
    text = upcoming(period="tomorrow", query="homework")
    assert "Homework 3" in text and "Reading: Chapter 4" not in text and "Office hours" not in text


# --- get_my_day_plan ------------------------------------------------------------------------

def test_day_plan_for_tomorrow_has_the_blocks_once_and_the_calendar_copy_gives_way():
    text = asyncio.run(proactive_tools.get_my_day_plan(day="tomorrow"))
    for title in ("Reading: Chapter 4", "Homework 3", "Review: 3 emails", "Lab report due"):
        assert text.count(title) == 1, (title, text)
    assert text.count("Team sync") == 1
    order(text, "Reading: Chapter 4", "Homework 3", "Review: 3 emails")
    assert "suggested time, not kept yet" in text


def test_day_plan_for_today_has_no_blocks_of_tomorrow():
    assert "Homework 3" not in asyncio.run(proactive_tools.get_my_day_plan(day="today"))


# --- the rows themselves ----------------------------------------------------------------------

def test_rows_for_a_range_cover_each_day_from_today_on():
    seen = []
    day_agenda.snapshot_fn = lambda day, tz: seen.append(day) or {"blocks": [], "deadlines": []}
    day_agenda.rows_sync(ts(2), ts(5), now=NOW.timestamp(), tz=TZ)
    assert seen == ["2026-11-02", "2026-11-03", "2026-11-04"]
    seen.clear()
    day_agenda.rows_sync(ts(1), ts(1) + 86400, now=NOW.timestamp(), tz=TZ)
    assert seen == []                                   # a day that is over is never planned
