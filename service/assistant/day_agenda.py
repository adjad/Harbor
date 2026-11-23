"""Day's planned blocks and due items, for the tools Ask reads a day with.

The Day tab and the calendar are one schedule, so "my day" has to hold both. A study
block, a block from a task the person kept and a block Harbor suggests (not yet kept)
live in Day's plan, not on the calendar, and Ask used to answer from calendar events
and Reminders alone. `rows` turns Day's plan for each date in a range into the same
row shape the schedule tools use (service/tools/assistant_tools.py), marked
``source: "day"``; `merge` adds them to a read's rows.

Rules:
- IMPORTANT: each item appears once. A block Harbor also wrote to the calendar as its own
  event (marked harbor-day://block/<id>, see harbor_events.py) is dropped from the calendar
  rows at ingestion, so it counts once; an older copy that is not marked, or an event the
  study "Add to calendar" button made, is matched by the block's event id or by title and
  start, and the block stands for it.
- IMPORTANT: this works with the calendar sync on or off: it reads Day's plan, never the
  calendar it may have written.
- IMPORTANT: a suggestion the person has not kept is labelled "suggested", never presented
  as planned. Calendar events, time Harbor only guesses is busy, and time away are not rows.
- Never fatal: a plan that can't be read adds nothing and the answer stands on the rest.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

STUDY_PREFIX = "study-block:"
KEPT_KINDS = frozenset({"study", "project", "task"})
MAX_DAYS = 8
BUDGET_S = 20.0


def _snapshot(day: str, timezone: str) -> dict:
    from service.assistant import today_api
    return today_api.snapshot(day, timezone)


# Tests replace this; production reads the same snapshot the Day tab does.
snapshot_fn = _snapshot


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _title(value) -> str:
    return " ".join(str(value or "").split())


def block_rows(snap: dict) -> list[dict]:
    """The rows for one day's snapshot: its blocks, then what is due."""
    proposed = ((snap.get("workspace") or {}).get("proposed_plan") or {})
    blocks = list(proposed.get("blocks") or snap.get("blocks") or [])
    have = {b.get("id") for b in blocks if isinstance(b, dict)}
    # Study blocks ride on the plan's own list beside the workspace's.
    blocks += [b for b in snap.get("blocks") or [] if isinstance(b, dict) and b.get("kind") == "study"
               and b.get("id") not in have]
    rows: list[dict] = []
    seen: set[str] = set()
    for block in blocks:
        if not isinstance(block, dict) or not _title(block.get("title")):
            continue
        kind, ident = block.get("kind"), str(block.get("id") or "")
        study = kind == "study" and ident.startswith(STUDY_PREFIX)
        kept = bool(block.get("task_id")) and kind in KEPT_KINDS
        suggested = kind == "suggestion"
        if not (study or kept or suggested) or ident in seen:
            continue
        start, end = block.get("start"), block.get("end")
        if not _finite(start) or block.get("all_day"):
            continue
        seen.add(ident)
        rows.append({"id": "day:" + ident, "source": "day", "kind": "block", "title": _title(block["title"]),
                     "when_ts": float(start), "end_ts": float(end) if _finite(end) and end >= start else None,
                     "all_day": False, "state": "suggested" if suggested else "planned",
                     "block_id": ident, "event_source_id": block.get("event_source_id") or None})
    for item in snap.get("deadlines") or []:
        if not isinstance(item, dict) or not _title(item.get("title")) or not _finite(item.get("due_ts")):
            continue
        rows.append({"id": "day:due:" + str(item.get("id") or item["title"]), "source": "day", "kind": "due",
                     "title": _title(item["title"]), "when_ts": float(item["due_ts"]), "end_ts": None,
                     "all_day": bool(item.get("all_day")), "state": "due", "block_id": None,
                     "event_source_id": None})
    return rows


def _days(start: float, end: float, now: float, tz: str) -> list[str]:
    zone = ZoneInfo(tz)
    first = datetime.fromtimestamp(max(start, now - 86400), zone).date()
    today = datetime.fromtimestamp(now, zone).date()
    first = max(first, today)
    last = datetime.fromtimestamp(end - 1, zone).date() if end > start else first
    days, day = [], first
    while day <= last and len(days) < MAX_DAYS:
        days.append(day.isoformat())
        day += timedelta(days=1)
    return days


def rows_sync(start: float, end: float, *, now: float, tz: str) -> list[dict]:
    """Day's rows in [start, end), for each date from today on."""
    out: list[dict] = []
    stop = time.monotonic() + BUDGET_S
    for day in _days(start, end, now, tz):
        if time.monotonic() > stop:
            break
        try:
            found = block_rows(snapshot_fn(day, tz))
        except Exception:  # noqa: BLE001 - one unreadable day never costs the answer
            log.debug("day plan unreadable for %s", day, exc_info=True)
            continue
        out.extend(r for r in found if start <= r["when_ts"] < end
                   or (r["end_ts"] and r["when_ts"] < start < r["end_ts"] and r["kind"] == "block"))
    return out


async def rows(start: float, end: float, *, now: float, tz: str | None = None) -> list[dict]:
    """`rows_sync` off the event loop, bounded in time. [] when Day's plan can't be read."""
    if tz is None:
        from service.proactive.cycle import _zone
        tz = _zone()
    try:
        return await asyncio.wait_for(asyncio.to_thread(rows_sync, start, end, now=now, tz=tz), timeout=BUDGET_S + 5)
    except Exception:  # noqa: BLE001
        return []


def _key(title, when_ts) -> tuple[str, int]:
    return (re.sub(r"\s+", " ", str(title or "")).strip().casefold(), int(round(float(when_ts or 0) / 60.0)))


def merge(items: list[dict], day_rows: list[dict]) -> list[dict]:
    """`items` plus Day's rows, each thing once.

    A calendar row that is the block already written to the calendar is dropped for the
    block: the same title and start minute, or the event id the study "Add to calendar"
    flow recorded. A Day due item that a Reminders row already lists is dropped for it."""
    if not day_rows:
        return items
    blocks = [r for r in day_rows if r["kind"] == "block"]
    block_keys = {_key(r["title"], r["when_ts"]) for r in blocks}
    event_ids = {r["event_source_id"] for r in blocks if r.get("event_source_id")}
    kept = []
    for item in items:
        if item.get("source") == "calendar" and (
                str(item.get("source_id") or "") in event_ids
                or _key(item.get("title"), item.get("when_ts")) in block_keys):
            continue
        kept.append(item)
    have = {_key(i.get("title"), i.get("when_ts")) for i in kept}
    have_titles = {_key(i.get("title"), 0)[0] for i in kept if i.get("source") != "calendar"}
    added = []
    for row in day_rows:
        key = _key(row["title"], row["when_ts"])
        if key in have:
            continue
        if row["kind"] == "due" and key[0] in have_titles:
            continue
        have.add(key)
        added.append(row)
    return kept + added


def note(row: dict) -> str:
    """The short label an agenda line carries for a Day row."""
    if row.get("kind") == "due":
        return "due"
    until = ""
    end = row.get("end_ts")
    if _finite(end):
        until = ", until " + datetime.fromtimestamp(end).strftime("%-I:%M %p")
    if row.get("state") == "suggested":
        return "suggested time, not kept yet" + until
    return "planned by Harbor" + until
