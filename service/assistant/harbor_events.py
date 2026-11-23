"""Calendar events Harbor wrote itself, and keeping them out of the person's meetings.

When the person turns on "add what Harbor plans to your calendar", Day's blocks are written
to a calendar as events carrying a marker: the event's link, and a line in its notes, both
``harbor-day://block/<block id>`` (app/Sources/HarborApp/CalendarReader.swift, DayCalendarMarker).
Day already shows those blocks, so no reader may show them again as meetings.

Rules:
- IMPORTANT: the Swift reader leaves marked events out of the events it posts and lists
  them separately (``harbor_events``). `is_marked` is the same test here, for any row that
  still arrives carrying a marker, so a row with one never reaches the commitments store.
- IMPORTANT: a calendar that syncs through an account (Google) can hand the same event
  back through the Google reader, which never reads notes. `record` keeps the title and
  start of the events Harbor wrote; google_calendar_source drops a Google row that is one.
- A person's own event, even one named like a block, is never dropped: only a marker, or
  a title and start Harbor wrote, makes an event Harbor's.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import time

from service import paths

log = logging.getLogger(__name__)

PREFIX = "harbor-day://block/"
_FIELDS = ("url", "notes", "description", "harbor_block_id")
MAX_KEPT = 2000
# Events that ended this long ago are forgotten.
KEEP_S = 3 * 86400


def _path():
    return paths.MOE_DIR / "harbor_calendar_events.json"


def is_marked(item: dict) -> bool:
    """Whether one calendar row is an event Harbor wrote, by its marker."""
    if not isinstance(item, dict):
        return False
    for field in _FIELDS:
        value = item.get(field)
        if isinstance(value, str) and PREFIX in value:
            return True
    return bool(item.get("harbor_block_id"))


def key(title, when_ts) -> tuple[str, int]:
    return (re.sub(r"\s+", " ", str(title or "")).strip().casefold(), int(round(float(when_ts or 0) / 60.0)))


def without_marked(items: list[dict]) -> list[dict]:
    return [item for item in items if not is_marked(item)]


def record(events) -> None:
    """Remember the events Harbor wrote, as the app last reported them (a whole replacement)."""
    now = time.time()
    rows = []
    for event in events if isinstance(events, list) else []:
        if not isinstance(event, dict) or not isinstance(event.get("block_id"), str):
            continue
        when, end = event.get("when_ts"), event.get("end_ts")
        if isinstance(when, bool) or not isinstance(when, (int, float)) or not math.isfinite(when):
            continue
        if isinstance(end, (int, float)) and not isinstance(end, bool) and math.isfinite(end) and end < now - KEEP_S:
            continue
        rows.append({"block_id": event["block_id"][:400], "title": str(event.get("title") or "")[:300],
                     "when_ts": float(when)})
    rows = rows[:MAX_KEPT]
    try:
        target = _path()
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows))
        os.replace(tmp, target)
    except OSError:
        log.debug("could not record Harbor calendar events", exc_info=True)


def recorded() -> list[dict]:
    try:
        data = json.loads(_path().read_text())
    except (OSError, ValueError):
        return []
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


def recorded_keys() -> set[tuple[str, int]]:
    return {key(r.get("title"), r.get("when_ts")) for r in recorded() if r.get("title")}


def without_recorded(items: list[dict], keys: set[tuple[str, int]] | None = None) -> list[dict]:
    """`items` minus rows that are an event Harbor wrote (by title and start), plus any marked row."""
    keys = recorded_keys() if keys is None else keys
    return [item for item in items if not is_marked(item) and key(item.get("title"), item.get("when_ts")) not in keys]
