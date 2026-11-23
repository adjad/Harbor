"""Ask's suggested prompts: questions built from the person's own cached data.

Ask's empty state used to offer generic questions. This offers up to four that
use what Harbor already holds on this Mac, and each one only when the cache that
answers it is present and recent enough, and only when the tool Ask's agent
would call for it can answer from that cache:

  due_week       "What's due this week?"                    get_upcoming(period="this week")
  mail_sender    "What did Priya say recently?"             view_emails(query=<first name>, strict_match=True)
  course         "What's left for STAT 101 this week?"      get_upcoming(period="this week", query="STAT 101")
  free_tomorrow  "When am I free tomorrow for two hours?"   find_free_time(period="tomorrow", minutes=120)
  reply          "Anything in my email I still need to reply to?"  summarize_emails()
  notes          "What's in my notes about <title>?"        search_notes(query=<title>)

Building the list reads only what is already in memory or in the local assistant
store (the calendar, reminders, mail header and body caches, the notes cache). It
never starts a model, a network request or a script, and nothing here is stored.
Each candidate is a pure function of that state and `now`, so tests drive it with
invented data. `nudge` (the route's query flag) is separate: it asks the Harbor app,
through the same event Ask's own tools use, to read a source again when that
source has no usable cache, so the list is complete the next time Ask opens.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from email.utils import parseaddr

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter(prefix="/assistant/ask", tags=["ask"])

LIMIT = 4
MAX_FROM_MAIL = 2

# How recent a source's last good read must be for a prompt about it. The app reads
# Calendar and Reminders every couple of minutes; Mail every five (hourly when Mail
# is closed and Harbor opens it); Notes once a day, and the service drops them at 24 h.
CALENDAR_MAX_AGE_S = 30 * 60.0
MAIL_HEADERS_MAX_AGE_S = 3 * 3600.0
MAIL_BODIES_MAX_AGE_S = 6 * 3600.0
NOTES_MAX_AGE_S = 24 * 3600.0
MAIL_LOOKBACK_S = 7 * 86400.0
NOTE_LOOKBACK_S = 30 * 86400.0
COURSE_LOOKBACK_DAYS = 45
COURSE_MIN_OCCURRENCES = 3

_NUDGE_EVERY_S = 90.0
_last_nudge = 0.0


class Suggestion(dict):
    """{id, prompt, source, tool, args}: `tool` and `args` say what Ask's agent
    calls to answer the prompt (for tests and debugging; the app shows `prompt`)."""


def _suggestion(sid: str, prompt: str, source: str, tool: str, args: dict) -> Suggestion:
    return Suggestion(id=sid, prompt=prompt, source=source, tool=tool, args=args)


# --- shared readiness ---------------------------------------------------------

def _store():
    from service.assistant.store import assistant_store
    return assistant_store


def _connector(source: str) -> dict:
    from service.assistant import scheduler
    return scheduler.connectors_status().get(source) or {}


def _native_ready(source: str, now: float, *, max_age: float = CALENDAR_MAX_AGE_S) -> bool:
    """The app has posted a good read of Calendar or Reminders recently."""
    info = _connector(source)
    stamp = info.get("last_sync")
    return (bool(info.get("available")) and not info.get("syncing")
            and isinstance(stamp, (int, float)) and -60 <= now - stamp <= max_age)


def _covers(now: float, until: float) -> bool:
    """Calendar's confirmed read range reaches `until` (find_free_time refuses otherwise)."""
    diagnostics = _connector("calendar").get("diagnostics") or {}
    lo, hi = diagnostics.get("coverage_start"), diagnostics.get("coverage_end")
    return (isinstance(lo, (int, float)) and isinstance(hi, (int, float))
            and lo <= now and hi >= until)


def _week_window(now: float) -> tuple[float, float]:
    from service.tools.timeranges import resolve_span
    start, end, _ = resolve_span("this week", now=datetime.fromtimestamp(now), forward=True)
    return max(start, now), end


def _holiday_free(rows: list[dict]) -> list[dict]:
    from service.tools.assistant_tools import _without_holiday_calendars
    return _without_holiday_calendars(rows, include_holidays=False)


def _schedule_rows(start: float, end: float, now: float) -> list[dict]:
    """What get_upcoming(period="this week") lists from the store for [start, end):
    Calendar events overlapping it, and other sources (Reminders, manual) due in it.
    A source whose last read is not recent is left out, as get_upcoming withholds it."""
    store = _store()
    rows = []
    if _native_ready("calendar", now):
        rows += store.calendar_events_overlapping(start, end)
    rows += [r for r in store.active_between(start, end, now=now)
             if r.get("source") != "calendar"
             and (r.get("source") != "reminders" or _native_ready("reminders", now))]
    return _holiday_free(rows)


# --- calendar and reminders ----------------------------------------------------

_DUE_WORDS = re.compile(r"\b(due|deadline|exam|midterm|final|quiz|test|assignment|homework|"
                        r"submit|submission|project|paper|report)\b", re.I)
_DUE_KINDS = {"reminder", "assignment", "exam", "task", "todo", "deadline"}


def _is_due_item(row: dict) -> bool:
    if row.get("source") in ("reminders", "manual") and row.get("kind") != "event":
        return True
    if row.get("kind") in _DUE_KINDS:
        return True
    # An all-day entry alone (a birthday, "Out of office") is not something due.
    return bool(_DUE_WORDS.search(str(row.get("title") or "")))


def due_week(now: float) -> Suggestion | None:
    start, end = _week_window(now)
    if end <= start:
        return None
    rows = [r for r in _schedule_rows(start, end, now) if _is_due_item(r)]
    if not rows:
        return None
    return _suggestion("due_week", "What's due this week?", "calendar", "get_upcoming",
                       {"period": "this week"})


_COURSE_CODE = re.compile(r"\b([A-Z]{2,5})[ -]?(\d{2,4}[A-Z]?)\b")


def _course_name(title: str) -> str | None:
    found = _COURSE_CODE.search(str(title or ""))
    return f"{found.group(1)} {found.group(2)}" if found else None


def course(now: float) -> Suggestion | None:
    """A course or project that recurs on the calendar (three or more events in the
    last six weeks and the next two) and has something left this week."""
    if not _native_ready("calendar", now):
        return None
    store = _store()
    seen = store.calendar_events_overlapping(now - COURSE_LOOKBACK_DAYS * 86400, now + 14 * 86400)
    counts: dict[str, int] = {}
    for row in _holiday_free(seen):
        name = _course_name(row.get("title"))
        if name:
            counts[name] = counts.get(name, 0) + 1
    start, end = _week_window(now)
    if end <= start:
        return None
    left = _schedule_rows(start, end, now)
    from service.tools.assistant_tools import _match_schedule_query
    for name, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        if n >= COURSE_MIN_OCCURRENCES and _match_schedule_query(left, name):
            return _suggestion("course", f"What's left for {name} this week?", "calendar",
                               "get_upcoming", {"period": "this week", "query": name})
    return None


def free_tomorrow(now: float) -> Suggestion | None:
    """Offered only when tomorrow (9 to 6) has a gap of two hours, so the answer is
    a time and never "no matches"."""
    from service.tools.assistant_tools import _is_holiday_calendar_item
    tomorrow = (datetime.fromtimestamp(now) + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    lo = tomorrow.replace(hour=9).timestamp()
    hi = tomorrow.replace(hour=18).timestamp()
    if not (_native_ready("calendar", now) and _native_ready("reminders", now)
            and _covers(now, tomorrow.timestamp() + 86400)):
        return None
    busy = []
    for row in _store().calendar_events_overlapping(tomorrow.timestamp(), tomorrow.timestamp() + 86400):
        if row.get("all_day") and row.get("calendar_writable") is False and _is_holiday_calendar_item(row):
            continue
        begin, finish = row.get("when_ts"), row.get("end_ts")
        if not isinstance(begin, (int, float)) or not isinstance(finish, (int, float)) or finish < begin:
            return None          # find_free_time refuses a calendar with missing ends
        if row.get("all_day") and finish == begin:
            return None
        a, b = max(begin, lo), min(finish, hi)
        if a < b:
            busy.append((a, b))
    cursor = lo
    for a, b in sorted(busy):
        if a - cursor >= 7200:
            break
        cursor = max(cursor, b)
    else:
        if hi - cursor < 7200:
            return None
    return _suggestion("free_tomorrow", "When am I free tomorrow for two hours?", "calendar",
                       "find_free_time", {"period": "tomorrow", "minutes": 120})


# --- mail ----------------------------------------------------------------------

def _mail_fresh(part: str, now: float, max_age: float) -> bool:
    from service.tools import email_tools as mail
    if mail.email_sync_state() != "ready":
        return False
    return any(isinstance(d.get("last_success"), (int, float)) and not d.get("error")
               and -60 <= now - d["last_success"] <= max_age
               for d in mail.email_source_details(part=part))


def _own_addresses() -> set[str]:
    from service.tools import email_tools as mail
    return {a.casefold() for a in mail.get_identity_emails()}


def _person_name(sender: str) -> str:
    """"Priya Nair <p@x.edu>" and "Nair, Priya" read as "Priya Nair"; an address alone as ""."""
    name, address = parseaddr(sender or "")
    if "<" not in (sender or "") and "@" not in (sender or ""):
        name = sender or ""          # the header cache keeps the bare display name
    name = re.sub(r"\s+", " ", (name or "").strip().strip("\"'"))
    if not name or "@" in name or not re.search(r"[A-Za-z]", name):
        return ""
    if "," in name:
        last, _, first = name.partition(",")
        name = f"{first.strip()} {last.strip()}".strip()
    return name


def _is_person(row: dict, own: set[str]) -> bool:
    from service.tools import email_tools as mail
    sender = str(row.get("sender") or "")
    address = (row.get("sender_address") or parseaddr(sender)[1]).casefold()
    probe = {"sender": sender, "sender_address": address,
             "message_id": row.get("message_id") or ""}
    return (bool(_person_name(sender)) and address not in own and not mail.is_machine_sender(sender)
            and not mail.is_broadcast(probe) and not mail.is_summary_noise(sender, str(row.get("subject") or "")))


def _sent_to() -> set[str]:
    """Addresses the user has mailed lately, from the sent rows already cached (Google's
    and Apple Mail's Sent list as the proactive cycle last read them). Reads nothing."""
    from service.proactive import sent_check
    from service.proactive.sources import cached_native_sent, sent_rows
    out: set[str] = set()
    for row in sent_rows() + cached_native_sent():
        out.update(address for _name, address in sent_check.recipients(row.get("to")))
    return out


def _in_contacts(address: str) -> bool:
    from service.tools.imessage_tools import contact_name_for_handle
    return bool(address) and bool(contact_name_for_handle(address))


def mail_sender(now: float) -> Suggestion | None:
    """"What did Priya say recently?" for a person who is in Contacts or whom the user has
    mailed. Only the first name goes into the prompt: a subject line is chosen by its
    sender, so it never reaches the panel. Needs the body cache, which view_emails reads."""
    from service.tools import email_tools as mail
    if not _mail_fresh("raw", now, MAIL_BODIES_MAX_AGE_S):
        return None
    own = _own_addresses()
    rows = [r for r in mail._parse_raw() if now - MAIL_LOOKBACK_S <= r["ts"] <= now + 3600]
    rows.sort(key=lambda r: r["ts"], reverse=True)
    replied = None
    for r in rows:
        if not _is_person(r, own):
            continue
        address = parseaddr(r["sender"])[1].casefold()
        if not address:
            continue
        if replied is None:
            replied = _sent_to()
        if address not in replied and not _in_contacts(address):
            continue
        first = _person_name(r["sender"]).split()[0]
        if len(first) >= 2:
            return _suggestion("mail_sender", f"What did {first} say recently?", "email",
                               "view_emails", {"query": first, "strict_match": True})
    return None


def reply(now: float) -> Suggestion | None:
    """Unread mail from a person in the last week."""
    from service.tools import email_tools as mail
    if not _mail_fresh("headers", now, MAIL_HEADERS_MAX_AGE_S):
        return None
    own = _own_addresses()
    for r in mail.header_rows(since_ts=now - MAIL_LOOKBACK_S):
        if r.get("unread") is True and r["ts"] <= now + 3600 and _is_person(r, own):
            return _suggestion("reply", "Anything in my email I still need to reply to?", "email",
                               "summarize_emails", {})
    return None


# --- notes ---------------------------------------------------------------------

_GENERIC_NOTE = re.compile(r"^(new note|untitled( note)?|note|notes|\d+)$", re.I)


def notes(now: float) -> Suggestion | None:
    from service.tools import notes_tools
    age = notes_tools.snapshot_age_seconds(now)
    if age is None or age > NOTES_MAX_AGE_S or notes_tools.notes_sync_state() != "ready":
        return None
    found = [n for n in notes_tools._parse()
             if now - NOTE_LOOKBACK_S <= n["ts"] <= now + 3600
             and len(n["body"]) >= 20 and n["folder"].casefold() != "recently deleted"]
    for n in sorted(found, key=lambda n: n["ts"], reverse=True):
        title = re.sub(r"\s+", " ", n["title"]).strip(" .:")
        if (4 <= len(title) <= 40 and not _GENERIC_NOTE.match(title)
                and sum(c.isalpha() for c in title) >= len(title) * 0.6):
            return _suggestion("notes", f"What's in my notes about {title}?", "notes",
                               "search_notes", {"query": title})
    return None


# --- the list ------------------------------------------------------------------

# Most useful first. Mail is capped so a person with a busy inbox still sees their week.
_ORDER = (due_week, mail_sender, course, free_tomorrow, reply, notes)


def build(now: float | None = None) -> list[Suggestion]:
    now = time.time() if now is None else now
    out: list[Suggestion] = []
    for candidate in _ORDER:
        if len(out) >= LIMIT:
            break
        try:
            item = candidate(now)
        except Exception:  # noqa: BLE001 — one source's odd data must not hide the others
            item = None
        if item is None:
            continue
        if item["source"] == "email" and sum(s["source"] == "email" for s in out) >= MAX_FROM_MAIL:
            continue
        out.append(item)
    return out


def _stale_sources(now: float) -> list[str]:
    """Sources with no usable cache that the app could read now (not denied, not mid-read)."""
    from service.assistant.sync_status import source_status
    wanted = []
    if not _native_ready("calendar", now):
        wanted.append("calendar")
    if not _native_ready("reminders", now):
        wanted.append("reminders")
    if not _mail_fresh("headers", now, MAIL_HEADERS_MAX_AGE_S):
        wanted.append("email")
    from service.tools import notes_tools
    age = notes_tools.snapshot_age_seconds(now)
    if age is None or age > NOTES_MAX_AGE_S:
        wanted.append("notes")
    return [s for s in wanted if source_status(s)["state"] == "ready"]


async def nudge(now: float | None = None) -> list[str]:
    """Ask the app to read the sources that have no usable cache. At most once every
    90 seconds; never a source that is denied or already being read."""
    global _last_nudge
    now = time.time() if now is None else now
    if now - _last_nudge < _NUDGE_EVERY_S:
        return []
    try:
        sources = _stale_sources(now)
        if not sources:
            return []
        from service.assistant.hub import hub
        _last_nudge = now
        await hub.publish({"type": "sync_assistant_sources_now", "sources": sources})
        return sources
    except Exception:  # noqa: BLE001 — a nudge is best effort
        return []


@router.get("/suggestions")
async def suggestions(request: Request, nudge_sources: bool = Query(False, alias="nudge")) -> dict:
    """The prompts for Ask's empty state, built from what is cached now. Harbor's own
    requests only: a page in a browser (it sends Origin) is refused, since the
    prompts name the person's contacts, courses and notes."""
    if "origin" in request.headers:
        raise HTTPException(403, "Ask's suggestions are for the Harbor app.")
    items = build()
    pushed = await nudge() if nudge_sources else []
    return {"prompts": [s["prompt"] for s in items], "items": items, "nudged": pushed}
