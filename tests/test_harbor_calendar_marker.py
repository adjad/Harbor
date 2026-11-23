"""Events Harbor writes to the calendar for Day's blocks never show as meetings.

The Swift sync marks each event it writes (link and notes line harbor-day://block/<id>, see
CalendarReader.swift DayCalendarMarker). Every calendar read path in the service drops them:
the Calendar sync route (the only way EventKit rows arrive) and the Google reader, which never
sees notes, by the title and start the app reported. A person's own event is never dropped.

Invented data only; no model, no server, no user store.
"""
from __future__ import annotations

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
    assert harbor_events.is_marked({"notes": "Room 4\nAdded by Harbor: " + MARK + "study-block%3A1"})
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
    own = [{"block_id": "task:t1", "title": "Homework 3", "when_ts": now, "end_ts": now + 3600}]
    assert sync([event("Office hours", now + 7200)], harbor_events=own).status_code == 200
    assert harbor_events.recorded_keys() == {harbor_events.key("Homework 3", now)}
    # An older app that sends no list leaves what was remembered alone; an empty list clears it.
    assert sync([event("Office hours", now + 7200)]).status_code == 200
    assert harbor_events.recorded_keys() == {harbor_events.key("Homework 3", now)}
    assert sync([event("Office hours", now + 7200)], harbor_events=[]).status_code == 200
    assert harbor_events.recorded_keys() == set()


def test_a_google_copy_of_a_harbor_event_is_not_a_second_meeting(store):
    now = time.time() + 7200
    sync([event("Office hours", now + 7200)], harbor_events=[
        {"block_id": "task:t1", "title": "Homework 3", "when_ts": now, "end_ts": now + 3600}])
    copy = {"source_id": "gcal:sam@example.com:g1", "kind": "meeting", "title": "Homework 3", "when_ts": now,
            "end_ts": now + 3600, "account": "sam@example.com"}
    own = {"source_id": "gcal:sam@example.com:g2", "kind": "meeting", "title": "Dentist", "when_ts": now,
           "end_ts": now + 3600, "account": "sam@example.com"}
    same_title_other_time = {**copy, "source_id": "gcal:sam@example.com:g3", "when_ts": now + 86400}
    with patch.object(G, "stored_items", return_value=[copy, own, same_title_other_time]):
        merged = G.merge_native_items([event("Office hours", now + 7200)], store)
    assert sorted(i["title"] for i in merged) == ["Dentist", "Homework 3", "Office hours"]
    assert [i for i in merged if i["title"] == "Homework 3"][0]["when_ts"] == now + 86400, "only the exact copy goes"


def test_google_refresh_drops_the_copy_too(store):
    now = time.time() + 7200
    harbor_events.record([{"block_id": "task:t1", "title": "Homework 3", "when_ts": now, "end_ts": now + 3600}])
    items = [{"source_id": G._prefix("sam@example.com") + "g1", "kind": "meeting", "title": "Homework 3",
              "when_ts": now, "end_ts": now + 3600},
             {"source_id": G._prefix("sam@example.com") + "g2", "kind": "meeting", "title": "Dentist",
              "when_ts": now, "end_ts": now + 3600}]
    assert G.store_events("sam@example.com", items, store) == 1
    assert [i["title"] for i in G.stored_items(store, {"sam@example.com"})] == ["Dentist"]
