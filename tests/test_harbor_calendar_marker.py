"""Events Harbor writes to the calendar for Day's blocks never show as meetings.

The Swift sync marks each event it writes (link and notes line harbor-day://block/<id>, see
CalendarReader.swift DayCalendarMarker). Every calendar read path in the service drops them:
the Calendar sync route (the only way EventKit rows arrive) and the Google reader, which never
sees notes, by the title and start the app reported. A person's own event is never dropped.

Invented data only; no model, no server, no user store.
"""
from __future__ import annotations

import os
import stat
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import service.main as main
from service import google_calendar_source as G
from service.assistant import harbor_events
from service.assistant.store import AssistantStore

MARK = "harbor-day://block/"


@pytest.fixture()
def store(tmp_path, monkeypatch):
    s = AssistantStore(tmp_path / "assistant.db")
    monkeypatch.setattr(main, "assistant_store", s)
    monkeypatch.setattr(harbor_events.paths, "MOE_DIR", tmp_path)
    return s


def sync(events, **extra):
    return TestClient(main.app).post("/assistant/sync/calendar", json={"source": "calendar", "events": events, **extra})


def event(title, when, **more):
    return {"kind": "event", "title": title, "when_ts": when, "end_ts": when + 3600, "source_id": "ev-" + title, **more}


def test_marker_is_recognised_by_link_or_notes_and_never_by_a_lookalike():
    assert harbor_events.is_marked({"url": MARK + "task%3Aabc"})
    assert harbor_events.is_marked({"notes": "Added by Harbor: " + MARK + "install-1/study-block%3A1\nRoom 4"})
    assert harbor_events.is_marked({"url": MARK + "install-1/task%3Aabc"})
    # Only the first line of notes, and only a link that starts the URL.
    assert not harbor_events.is_marked({"notes": "Room 4\nAdded by Harbor: " + MARK + "study-block%3A1"})
    assert not harbor_events.is_marked({"url": "https://example.com/?" + MARK + "x"})
    assert harbor_events.is_marked({"harbor_block_id": "task:abc"})
    assert not harbor_events.is_marked({"title": "Reading: STAT 101", "url": "https://example.com/x", "notes": "Agenda"})
    assert not harbor_events.is_marked({"title": "harbor-day block", "location": "Room 4"})
    assert not harbor_events.is_marked({})


def test_the_sync_route_keeps_meetings_and_drops_marked_events(store):
    now = time.time() + 7200
    events = [event("Office hours", now),
              event("Reading: STAT 101", now + 7200, url=MARK + "study-block%3Aa1"),
              event("Homework 3", now + 14400, notes="Added by Harbor: " + MARK + "task%3At1")]
    response = sync(events)
    assert response.status_code == 200 and response.json()["synced"] == 1, response.text
    assert [r["title"] for r in store.upcoming(time.time(), days=3)] == ["Office hours"]


def test_the_app_lists_its_own_events_apart_and_they_are_remembered(store):
    now = time.time() + 7200
    own = [{"block_id": "task:t1", "title": "Homework 3", "when_ts": now, "end_ts": now + 3600, "calendar": "Sam"}]
    assert sync([event("Office hours", now + 7200)], harbor_events=own).status_code == 200
    expected = {(*harbor_events.key("Homework 3", now), int(round((now + 3600) / 60.0)), "sam")}
    assert harbor_events.recorded_keys() == expected
    # An older app that sends no list leaves what was remembered alone; an empty list clears it.
    assert sync([event("Office hours", now + 7200)]).status_code == 200
    assert harbor_events.recorded_keys() == expected
    assert sync([event("Office hours", now + 7200)], harbor_events=[]).status_code == 200
    assert harbor_events.recorded_keys() == set()


def google(title, when, *, end=None, calendar="Sam", sid="g1"):
    return {"source_id": "gcal:sam@example.com:" + sid, "kind": "meeting", "title": title, "when_ts": when,
            "end_ts": when + 3600 if end is None else end, "context": calendar, "account": "sam@example.com"}


def test_a_google_copy_of_a_harbor_event_is_not_a_second_meeting(store):
    now = time.time() + 7200
    sync([event("Office hours", now + 7200)], harbor_events=[
        {"block_id": "task:t1", "title": "Homework 3", "when_ts": now, "end_ts": now + 3600, "calendar": "Sam"}])
    copy, own = google("Homework 3", now, sid="g1"), google("Dentist", now, sid="g2")
    later = google("Homework 3", now + 86400, sid="g3")
    with patch.object(G, "stored_items", return_value=[copy, own, later]):
        merged = G.merge_native_items([event("Office hours", now + 7200)], store)
    assert sorted(i["title"] for i in merged) == ["Dentist", "Homework 3", "Office hours"]
    assert [i for i in merged if i["title"] == "Homework 3"][0]["when_ts"] == now + 86400, "only the exact copy goes"


def test_a_real_google_meeting_is_hidden_only_when_title_start_end_and_calendar_all_match(store):
    now = time.time() + 7200
    harbor_events.record([{"block_id": "task:t1", "title": "Homework 3", "when_ts": now, "end_ts": now + 3600,
                         "calendar": "Sam"}])
    meetings = [google("Homework 3", now, calendar="Work", sid="a"),           # another calendar
                google("Homework 3", now, end=now + 5400, sid="b"),            # another end
                google("Homework 3", now, calendar="", sid="c"),               # no calendar named
                google("Homework 3", now, sid="d")]                            # the copy
    kept = harbor_events.without_recorded(meetings)
    assert [m["source_id"][-1] for m in kept] == ["a", "b", "c"]
    # An app that reported no calendar or end for its events hides nothing.
    harbor_events.record([{"block_id": "task:t1", "title": "Homework 3", "when_ts": now, "end_ts": now + 3600}])
    assert len(harbor_events.without_recorded(meetings)) == 4


def test_google_refresh_drops_the_copy_too(store):
    now = time.time() + 7200
    harbor_events.record([{"block_id": "task:t1", "title": "Homework 3", "when_ts": now, "end_ts": now + 3600,
                         "calendar": "Sam"}])
    items = [{**google("Homework 3", now, sid="g1"), "source_id": G._prefix("sam@example.com") + "g1"},
             {**google("Dentist", now, sid="g2"), "source_id": G._prefix("sam@example.com") + "g2"}]
    assert G.store_events("sam@example.com", items, store) == 1
    assert [i["title"] for i in G.stored_items(store, {"sam@example.com"})] == ["Dentist"]


def test_the_recorded_events_file_is_owner_only(store):
    now = time.time() + 7200
    old = os.umask(0o022)
    try:
        harbor_events.record([{"block_id": "task:t1", "title": "Homework 3", "when_ts": now, "end_ts": now + 3600}])
    finally:
        os.umask(old)
    target = harbor_events._path()
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
    assert [e["title"] for e in harbor_events.recorded()] == ["Homework 3"]


def test_a_file_an_older_build_left_readable_is_tightened_on_read(store):
    harbor_events.record([{"block_id": "task:t1", "title": "Homework 3", "when_ts": time.time() + 7200}])
    target = harbor_events._path()
    os.chmod(target, 0o644)
    assert harbor_events.recorded()
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
