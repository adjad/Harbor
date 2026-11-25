"""The Brief after a long gap: say it is still building, and let it go first.

After a night or a day away the first Brief is made from what was cached
before the gap: mail, texts, calendar and the ready-for-you drafts all come in
over the next minute or two. Until they do, the Brief must not pass that off
as current, and it must not say "nothing needs you" when it just hasn't read
yet. This module opens a short BUILDING WINDOW on a launch or wake after a long
gap, tracks each source the Brief depends on, and closes the window when every
one has come in, or when it times out.

The thresholds (written down here, as asked):
- LONG_GAP_S, 6 hours: the last Brief cycle of any kind (state "as_of") is
  this old when the service starts or the notch opens. The light by-day ticks
  keep as_of current while the Mac is awake, so only a real absence (Harbor quit,
  the Mac asleep or off) reaches it. A Brief that was never built is the
  first-Brief path's job ("Building your first Brief…"), not this one.
- SLEEP_GAP_S, 3 hours: the scheduler ticks every 30 s, so a wall-clock jump
  this long between two ticks is the Mac waking from sleep.
- GIVE_UP_S, 3 minutes: the longest a window stays open. After it, the Brief
  shows what is there and names the sources that didn't come in ("late") until
  they do, for at most LATE_FOR_S.
- RECENT_S, 10 minutes: a source whose last good read is this recent counts as
  in, even from before the window (a short quit, say).

What counts as "in" for each source (read from the readers' own stamps; no
reader is changed and nothing here reads a source or runs a model):
- calendar, reminders: the app's sync report (scheduler.record_sync) arrived,
  not syncing, recently. A report that it can't be read is in as well: that is
  terminal, and the Brief's unread rows already say why.
- email: every connected Google account fetched its headers recently (or
  reported an error), or, with none connected, Mail.app's headers did. Mail
  that is unavailable is in (terminal, honest() names it).
- messages: the app's chat.db read (imessage_tools.cache_messages) arrived
  recently, readable or not.
- day: Day plans from calendar and reminders, so it is in when both are.
- drafts: a proactive cycle (cards and drafts) finished after mail and messages
  came in. While one runs, it is not in yet.
A source turned off or with no permission is never waited for.

Priority: while a window is open, scheduled background model work waits, through
the gate it already obeys (Demo mode's background hold: behavior learning and
its use-log/writing-profile passes, the behavior phraser, memory capture, mail
memory, the attachment index). The Brief's own work (the cycle, its catch-up,
the gists) does not ask that gate's background half. A live turn still beats
everything: the cycle and the catch-up stand down for it as they always have.
The hold can't outlast GIVE_UP_S.
"""
from __future__ import annotations

import time

LONG_GAP_S = 6 * 3600
SLEEP_GAP_S = 3 * 3600
GIVE_UP_S = 3 * 60
RECENT_S = 10 * 60
LATE_FOR_S = 30 * 60
# The catch-up's settle wait inside a window (return_refresh.SETTLE_S is 45 s).
SETTLE_S = 20
# Re-evaluate at most this often (the background hold is asked by several loops).
_CACHE_S = 2.0

SOURCES = ("email", "messages", "calendar", "reminders", "day", "drafts")
LABELS = {"email": "Mail", "messages": "Messages", "calendar": "Calendar",
          "reminders": "Reminders", "day": "Day", "drafts": "Drafts"}

_window: dict | None = None      # {"reason", "started", "deadline"}
_late: dict | None = None        # {"sources": [...], "until": ts} after a timeout
_last_tick: float | None = None
_launch_checked = False
_cache: tuple[float, dict] | None = None


def _log(text: str) -> None:
    print(f"[proactive] brief warmup: {text}", flush=True)


def _stamp(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if value > 0 else None


def _store_state(store=None) -> dict:
    try:
        if store is None:
            from service.proactive.store import store as default_store
            store = default_store()
        return store.state() or {}
    except Exception:  # noqa: BLE001 — unreadable: as if never built
        return {}


def long_gap(now: float, state: dict) -> bool:
    """The last Brief cycle is LONG_GAP_S old. Never built is not a gap."""
    as_of = _stamp(state.get("as_of"))
    return as_of is not None and now - as_of >= LONG_GAP_S


# --- opening and closing -------------------------------------------------------


def begin(reason: str, *, now: float | None = None) -> bool:
    """Open a window (one already open is kept). True when one is open."""
    global _window, _late, _cache
    now = time.time() if now is None else now
    if _window is None:
        _window = {"reason": reason, "started": now, "deadline": now + GIVE_UP_S}
        _late = None
        _cache = None
        _log(f"building ({reason})")
    return True


def consider(reason: str, *, now: float | None = None, state: dict | None = None, store=None) -> bool:
    """Open a window when the Brief's last cycle is LONG_GAP_S old."""
    now = time.time() if now is None else now
    state = _store_state(store) if state is None else state
    if long_gap(now, state):
        return begin(reason, now=now)
    return _window is not None


def tick(*, now: float | None = None, store=None) -> None:
    """Each scheduler tick: the first one after the service started, and a
    wake (a wall-clock jump of SLEEP_GAP_S since the last tick). Never raises."""
    global _last_tick, _launch_checked
    try:
        now = time.time() if now is None else now
        previous, _last_tick = _last_tick, now
        if not _launch_checked:
            _launch_checked = True
            consider("launch", now=now, store=store)
        elif previous is not None and now - previous >= SLEEP_GAP_S \
                and _stamp(_store_state(store).get("as_of")) is not None:
            begin("wake", now=now)
        _evaluate(now, store)
    except Exception:  # noqa: BLE001 — the Brief shows as it did before
        pass


# --- the sources ---------------------------------------------------------------


def _fresh(stamp, started: float, now: float) -> bool:
    stamp = _stamp(stamp)
    return stamp is not None and (stamp >= started or 0 <= now - stamp <= RECENT_S)


def _native(source: str, started: float, now: float, status=None) -> tuple[bool, float | None]:
    """(in, stamp) for Calendar or Reminders from the app's sync report."""
    if status is None:
        from service.assistant import scheduler
        status = scheduler.connectors_status()
    raw = status.get(source) or {}
    stamp = _stamp(raw.get("last_sync"))
    if raw.get("syncing") or not _fresh(stamp, started, now):
        return False, stamp
    return True, stamp


def _email(started: float, now: float) -> tuple[bool, float | None]:
    from service.tools import email_tools as mail
    if mail.email_sync_state() == "unavailable":
        return True, None            # terminal: honest() lists it as unread
    accounts = dict(getattr(mail, "_external_accounts", {}) or {})
    if accounts:
        stamps = []
        for item in accounts.values():
            if item.get("headers_error"):
                continue             # can't be read: mail_unread_by names it
            stamp = _stamp(item.get("headers_at"))
            if not _fresh(stamp, started, now):
                return False, None
            stamps.append(stamp)
        return True, max(stamps) if stamps else None
    if getattr(mail, "_email_sync_pending", False):
        return False, None
    stamp = _stamp(getattr(mail, "_native_headers_at", None))
    return _fresh(stamp, started, now), stamp


def _messages(started: float, now: float) -> tuple[bool, float | None]:
    from service.tools import imessage_tools as messages
    if not getattr(messages, "_sync_completed", False):
        return False, None
    stamp = _stamp(getattr(messages, "_snapshot_captured_at", None))
    return _fresh(stamp, started, now), stamp


def _drafts(started: float, now: float, after: float, state: dict) -> bool:
    try:
        from service.proactive import cycle
        if cycle.running():
            return False
    except Exception:  # noqa: BLE001
        pass
    as_of = _stamp(state.get("as_of"))
    return as_of is not None and as_of >= after and _fresh(as_of, started, now)


def pending(now: float | None = None, *, store=None, readers=None) -> list[str]:
    """The sources not in yet, in SOURCES order. [] with no window open.
    ``readers`` ({source: callable(started, now) -> (in, stamp)}) is for tests."""
    if _window is None:
        return []
    return _waiting(_window["started"], time.time() if now is None else now, store=store, readers=readers)


def _waiting(started: float, now: float, *, store=None, readers=None) -> list[str]:
    readers = readers or {}
    state = _store_state(store)
    result: dict[str, tuple[bool, float | None]] = {}
    for source, read in (("email", _email), ("messages", _messages),
                         ("calendar", lambda s, n: _native("calendar", s, n)),
                         ("reminders", lambda s, n: _native("reminders", s, n))):
        try:
            result[source] = (readers.get(source) or read)(started, now)
        except Exception:  # noqa: BLE001 — unknown: not in yet (the timeout ends the wait)
            result[source] = (False, None)
    waiting = [s for s in ("email", "messages", "calendar", "reminders") if not result[s][0]]
    if "calendar" in waiting or "reminders" in waiting:
        waiting.append("day")
    after = max([started] + [stamp for ok, stamp in (result["email"], result["messages"]) if ok and stamp])
    try:
        drafts_in = (readers.get("drafts") or (lambda s, n: _drafts(s, n, after, state)))(started, now)
    except Exception:  # noqa: BLE001
        drafts_in = False
    if "email" in waiting or "messages" in waiting or not drafts_in:
        waiting.append("drafts")
    return [s for s in SOURCES if s in waiting]


def _evaluate(now: float, store=None, readers=None) -> dict:
    """Close the window when everything is in or time is up; the view."""
    global _window, _late, _cache
    if readers is None and _cache is not None and 0 <= now - _cache[0] < _CACHE_S:
        return _cache[1]
    view = {"building": False, "building_for": [], "late": []}
    if _window is not None:
        waiting = pending(now, store=store, readers=readers)
        if not waiting:
            _log(f"complete ({_window['reason']}, {now - _window['started']:.0f} s)")
            _window, _late = None, None
        elif now >= _window["deadline"]:
            _log("gave up waiting for " + ", ".join(waiting))
            _late = {"sources": waiting, "until": now + LATE_FOR_S, "started": _window["started"]}
            _window = None
        else:
            view.update(building=True, building_for=waiting)
    if _window is None and _late is not None:
        if now >= _late["until"]:
            _late = None
        else:
            # A late source drops off once it comes in.
            still = [s for s in _waiting(_late["started"], now, store=store, readers=readers)
                     if s in _late["sources"]]
            _late["sources"] = still
            if not still:
                _late = None
            view["late"] = list(still)
    _cache = (now, view)
    return view


def view(now: float | None = None, *, store=None) -> dict:
    """What workspace.proactive carries: ``building`` (the window is open),
    ``building_for`` (source ids not in yet) and ``late`` (after a timeout, the
    ones that still haven't come in). Never raises."""
    try:
        return dict(_evaluate(time.time() if now is None else now, store))
    except Exception:  # noqa: BLE001
        return {"building": False, "building_for": [], "late": []}


def building(now: float | None = None) -> bool:
    return view(now)["building"]


def wants_cycle(now: float | None = None) -> bool:
    """Mail and messages are in and the drafts aren't: the next cycle tick may
    run now instead of at its usual gap (the chat and turn rules still hold)."""
    v = view(now)
    waiting = v["building_for"]
    return v["building"] and "drafts" in waiting and "email" not in waiting and "messages" not in waiting


def holds_scheduled_work(now: float | None = None) -> bool:
    """Scheduled background model work waits while the Brief builds after a long gap."""
    return building(now)


def settle_s(default: float) -> float:
    """The catch-up's settle wait: shorter while the Brief is building."""
    return min(default, SETTLE_S) if _window is not None else default


def status() -> dict:
    return {"window": dict(_window) if _window else None, **view()}


def _reset_for_tests() -> None:
    global _window, _late, _last_tick, _launch_checked, _cache
    _window, _late, _last_tick, _launch_checked, _cache = None, None, None, False, None
