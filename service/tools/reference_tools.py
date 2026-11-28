"""Time, dates and time zones — computed locally, never recalled.

WHY THE CLOCK NEEDS A TOOL
--------------------------
A language model has no clock. Asked "what time is it", it will produce a
plausible time with complete confidence, and there is nothing in the answer to
indicate it was invented — the same class of failure as the arithmetic case in
`conversions.py`, and just as easy to eliminate.

Time-zone conversion has the same property plus a second trap: DST. "What time
is it in London" is not a fixed offset from here, it depends on the date and on
two separate DST calendars. `zoneinfo` reads the IANA database that ships with
the OS, so this is exact and needs no network.
"""
from __future__ import annotations

import datetime as dt
import re
from zoneinfo import ZoneInfo, available_timezones

from service.tools.registry import register

# Cities people name, mapped to IANA zones. `available_timezones()` covers the
# formal names ("Europe/London"); this covers how anyone actually asks.
_CITIES = {
    "london": "Europe/London", "paris": "Europe/Paris", "berlin": "Europe/Berlin",
    "madrid": "Europe/Madrid", "rome": "Europe/Rome", "amsterdam": "Europe/Amsterdam",
    "dublin": "Europe/Dublin", "lisbon": "Europe/Lisbon", "zurich": "Europe/Zurich",
    "stockholm": "Europe/Stockholm", "oslo": "Europe/Oslo", "moscow": "Europe/Moscow",
    "istanbul": "Europe/Istanbul", "athens": "Europe/Athens",
    "new york": "America/New_York", "nyc": "America/New_York",
    "boston": "America/New_York", "washington": "America/New_York",
    "miami": "America/New_York", "atlanta": "America/New_York",
    "toronto": "America/Toronto", "montreal": "America/Toronto",
    "chicago": "America/Chicago", "dallas": "America/Chicago",
    "houston": "America/Chicago", "austin": "America/Chicago",
    "denver": "America/Denver", "phoenix": "America/Phoenix",
    "los angeles": "America/Los_Angeles", "la": "America/Los_Angeles",
    "san francisco": "America/Los_Angeles", "sf": "America/Los_Angeles",
    "seattle": "America/Los_Angeles", "vancouver": "America/Vancouver",
    "mexico city": "America/Mexico_City", "sao paulo": "America/Sao_Paulo",
    "buenos aires": "America/Argentina/Buenos_Aires",
    "tokyo": "Asia/Tokyo", "osaka": "Asia/Tokyo", "seoul": "Asia/Seoul",
    "beijing": "Asia/Shanghai", "shanghai": "Asia/Shanghai",
    "hong kong": "Asia/Hong_Kong", "singapore": "Asia/Singapore",
    "bangkok": "Asia/Bangkok", "jakarta": "Asia/Jakarta",
    "mumbai": "Asia/Kolkata", "delhi": "Asia/Kolkata",
    "new delhi": "Asia/Kolkata", "bangalore": "Asia/Kolkata",
    "bengaluru": "Asia/Kolkata", "kolkata": "Asia/Kolkata",
    "dubai": "Asia/Dubai", "tel aviv": "Asia/Jerusalem",
    "jerusalem": "Asia/Jerusalem", "karachi": "Asia/Karachi",
    "sydney": "Australia/Sydney", "melbourne": "Australia/Melbourne",
    "brisbane": "Australia/Brisbane", "perth": "Australia/Perth",
    "auckland": "Pacific/Auckland",
    "johannesburg": "Africa/Johannesburg", "cairo": "Africa/Cairo",
    "lagos": "Africa/Lagos", "nairobi": "Africa/Nairobi",
    "honolulu": "Pacific/Honolulu", "anchorage": "America/Anchorage",
    "utc": "UTC", "gmt": "UTC",
}


def _resolve_zone(place: str) -> ZoneInfo | None:
    key = re.sub(r"\s+", " ", (place or "").strip().lower())
    key = re.sub(r"^(?:in|at|the)\s+", "", key)
    if not key:
        return None
    if key in _CITIES:
        return ZoneInfo(_CITIES[key])
    # An exact IANA name, however the user cased it.
    for zone in available_timezones():
        if zone.lower() == key:
            return ZoneInfo(zone)
    # Last resort: the city segment of an IANA name ("Kolkata" -> Asia/Kolkata).
    want = key.replace(" ", "_")
    for zone in sorted(available_timezones()):
        if zone.lower().rsplit("/", 1)[-1] == want:
            return ZoneInfo(zone)
    return None


def _fmt(when: dt.datetime) -> str:
    return when.strftime("%-I:%M %p on %A, %B %-d, %Y")


_DISPLAY = {"sf": "San Francisco", "la": "Los Angeles", "nyc": "New York",
            "utc": "UTC", "gmt": "GMT"}


def _label(place: str, zone: ZoneInfo) -> str:
    """Display name for a place: the one the user asked for.

    A city the user named ("Boston") is never replaced by the IANA zone's city
    ("New York"). Short abbreviations are expanded ("sf" -> San Francisco, not
    "Sf"), and an IANA name they typed ("Europe/London") shows its own city.
    """
    key = re.sub(r"\s+", " ", (place or "").strip().lower())
    key = re.sub(r"^(?:in|at|the)\s+", "", key)
    if key in _DISPLAY:
        return _DISPLAY[key]
    if key and "/" not in key:
        return key.title()
    city = str(zone).rsplit("/", 1)[-1].replace("_", " ")
    return city if city.upper() == "UTC" else city.title()


def _span(seconds: float, *, compact: bool = False) -> str:
    """A whole-minute offset as '3 hours' / '5 h 45 min' (never 5.8 hours)."""
    minutes = int(round(abs(seconds) / 60))
    h, m = divmod(minutes, 60)
    if m == 0:
        return f"{h}h" if compact else f"{h} hour{'s' if h != 1 else ''}"
    if h == 0:
        return f"{m} min"
    return f"{h} h {m} min"


@register(
    "world_time",
    "Get the current date and time — here, or in another city. Use this for "
    "ANY 'what time is it', 'what's today's date', or 'what time is it in X' "
    "question rather than answering from memory; you have no clock of your own. "
    "Also computes the difference between two places when both are given.",
    {
        "type": "object",
        "properties": {
            "place": {"type": "string",
                      "description": "City or IANA zone to report, e.g. 'Tokyo' or 'Europe/London'. Omit for local time."},
            "compare_to": {"type": "string",
                           "description": "Optional second place — returns the offset between the two."},
        },
    },
    category="compute",
    aliases=["what time is it", "what's today's date", "what day is it",
             "what time is it in tokyo right now", "how far ahead is london",
             "is it still monday in california", "what's the time difference with india"],
)
def world_time(place: str = "", compare_to: str = "") -> str:
    local_now = dt.datetime.now().astimezone()

    if not place and not compare_to:
        return f"It's {_fmt(local_now)} ({local_now.tzname()})."

    if place and compare_to:
        a, b = _resolve_zone(place), _resolve_zone(compare_to)
        if a is None:
            return f"(error: I don't know a place called {place!r}.)"
        if b is None:
            return f"(error: I don't know a place called {compare_to!r}.)"
        now_a = local_now.astimezone(a)
        now_b = local_now.astimezone(b)
        # Compare the UTC offsets at THIS instant, not the zones' nominal
        # offsets — that is what makes the answer correct across a DST boundary
        # where one side has changed over and the other hasn't yet.
        delta = (now_a.utcoffset() or dt.timedelta()) - (now_b.utcoffset() or dt.timedelta())
        hours = delta.total_seconds() / 3600
        if hours == 0:
            rel = "the same time as"
        else:
            rel = f"{_span(delta.total_seconds())} {'ahead of' if hours > 0 else 'behind'}"
        la, lb = _label(place, a), _label(compare_to, b)
        return (f"{la} is {rel} {lb}.\n"
                f"  {la}: {_fmt(now_a)}\n"
                f"  {lb}: {_fmt(now_b)}")

    target = place or compare_to
    zone = _resolve_zone(target)
    if zone is None:
        return (f"(error: I don't know a place called {target!r}. "
                f"Try a major city or an IANA zone like 'Europe/London'.)")
    there = local_now.astimezone(zone)
    delta = ((there.utcoffset() or dt.timedelta())
             - (local_now.utcoffset() or dt.timedelta())).total_seconds() / 3600
    if delta == 0:
        rel = "same as here"
    else:
        rel = f"{_span(delta * 3600, compact=True)} {'ahead of' if delta > 0 else 'behind'} you"
    return f"In {_label(target, zone)} it's {_fmt(there)} ({rel})."


