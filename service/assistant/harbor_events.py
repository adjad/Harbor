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
  back through the Google reader, which never reads notes. `record` keeps the title, start,
  end and calendar of the events Harbor wrote; google_calendar_source drops a Google row only
  when all four match.
- A person's own event, even one named like a block, is never dropped: only a marker, or
  the same title, start, end AND calendar as one Harbor wrote, makes an event Harbor's.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import stat
import time

from service import paths

log = logging.getLogger(__name__)

PREFIX = "harbor-day://block/"
MAX_KEPT = 2000
# Events that ended this long ago are forgotten.
KEEP_S = 3 * 86400


def _path():
    return paths.MOE_DIR / "harbor_calendar_events.json"


def is_marked(item: dict) -> bool:
    """Whether one calendar row is an event Harbor wrote, by its marker."""
    if not isinstance(item, dict):
        return False
    # The link must start the URL; in notes the marker is the first line only (the app writes it
    # there), so a marker further down someone's notes is not one.
    url = item.get("url")
    if isinstance(url, str) and url.startswith(PREFIX):
        return True
    for field in ("notes", "description"):
        value = item.get(field)
        if isinstance(value, str) and PREFIX in value.split("\n", 1)[0]:
            return True
    return bool(item.get("harbor_block_id"))


def _num(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


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
                     "when_ts": float(when), "end_ts": _num(end),
                     "calendar": str(event.get("calendar") or "")[:300]})
    rows = rows[:MAX_KEPT]
    try:
        # Titles and times of the person's day: owner-only from the first byte.
        paths.private_write_bytes(_path(), json.dumps(rows).encode("utf-8"))
    except OSError:
        log.debug("could not record Harbor calendar events", exc_info=True)


def _tighten(target) -> None:
    """A file an older build wrote with the default mode becomes owner-only on the first read."""
    try:
        info = os.lstat(target)
        if stat.S_ISREG(info.st_mode) and info.st_mode & 0o077:
            os.chmod(target, 0o600)
    except OSError:
        pass


def recorded() -> list[dict]:
    _tighten(_path())
    try:
        data = json.loads(_path().read_text())
    except (OSError, ValueError):
        return []
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


def _calendar_key(value) -> str:
    return " ".join(str(value or "").split()).casefold()


def recorded_keys() -> set[tuple]:
    """(title, start minute, end minute, calendar) of each event Harbor wrote. An event the app
    reported with no calendar name or no end is not matched at all."""
    out = set()
    for r in recorded():
        end, calendar = _num(r.get("end_ts")), _calendar_key(r.get("calendar"))
        if r.get("title") and end is not None and calendar:
            out.add((*key(r["title"], r.get("when_ts")), int(round(end / 60.0)), calendar))
    return out


def _row_key(item: dict):
    end = _num(item.get("end_ts"))
    if end is None:
        return None
    return (*key(item.get("title"), item.get("when_ts")), int(round(end / 60.0)), _calendar_key(item.get("context")))


def without_recorded(items: list[dict], keys: set | None = None) -> list[dict]:
    """`items` minus rows that are an event Harbor wrote (same title, start, end and calendar), plus
    any marked row."""
    keys = recorded_keys() if keys is None else keys
    return [item for item in items if not is_marked(item) and _row_key(item) not in keys]
