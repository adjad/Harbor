"""The Brief after a long gap: say it is still building, and let it go first.

After a night or a day away the first Brief is made from what was cached
before the gap: mail, texts, calendar and the ready-for-you drafts all come in
over the next minute or two. Until they do, the Brief must not pass that off
as current, and it must not say "nothing needs you" when it just hasn't read
yet. This module opens a short BUILDING WINDOW on a launch or wake after a long
gap, tracks each source the Brief depends on, and closes the window when every
one has come in, or when it times out.

The thresholds (written down here, as asked):
- LONG_GAP_S, 6 hours: at the service's first scheduler tick (a launch), the
  last Brief cycle of any kind (state "as_of") is this old. The light by-day
  ticks keep as_of current while the Mac is awake, so only a real absence
  reaches it. A Brief that was never built is the first-Brief path's job
  ("Building your first Brief…"), not this one.
- SLEEP_GAP_S, 3 hours: the scheduler ticks every 30 s, so a wall-clock jump
  this long between two ticks is the Mac waking from sleep.
- GIVE_UP_S, 3 minutes: the longest a window stays open (a monotonic deadline,
  so a clock change after a wake can't stretch it). After it, the Brief shows
  what is there and names the sources that didn't come in ("late") until they
  do, for at most LATE_FOR_S.
- SILENT_S, 90 seconds: a source that has reported nothing at all since the
  window opened leaves the wait (and is named late) instead of holding it for
  the full three minutes. Gmail's loop re-reads within about 90 s of a launch
  or wake (it sleeps 60 s, a fetch may take 30 s); the app's Calendar,
  Reminders, Mail.app and Messages reads come in seconds.

ONCE PER GAP. A window opens only at a launch or a wake, never on a notch
open, an unpark or any later read: after one completes or times out, none
opens again until the next launch or wake. A wake while one is still open (the
Mac slept through it) replaces it with a fresh one. None opens when the automatic cycle
is paused (a remote model without parity, cycle._remote_background_paused):
the drafts could never come in.

READS COUNT FROM the moment the gap ended, not from when the window opened:
the service's start for a launch (scheduler.STARTED_AT), and for a wake two
scheduler ticks after the last tick before the jump (the Mac was asleep until
at least then). So a read the app made in the seconds after a wake, before the
next scheduler tick opened the window, counts.

THE WAIT SET is fixed when the window opens. A source already terminal then is
left out: turned off, no permission, or unavailable (the Brief's unread rows
say why). The drafts' target is fixed the first time mail and messages are both
in, so a routine re-read after the cycle never sends the drafts back to waiting.

What counts as "in" (read from the readers' own stamps; no reader is changed,
and nothing here reads a source or runs a model). Only a read that completes
after the gap ended counts:
- calendar, reminders: the app's sync report (scheduler.record_sync) arrived
  after the gap ended and isn't "syncing". A report that it can't be read
  is in as well: that is terminal.
- email: every connected Google account fetched its headers after the gap
  ended. An error is never a read. A temporary one (offline, timed out) is
  waited for like any fetch still owed, but only while no other account has
  read: once every account has read or failed since the gap ended (its
  headers_error_at, email_tools.external_email_error), only the healthy ones
  are waited on. An error from before the gap is not a failure in it. An account whose sign-in stops mail
  (cycle._unread_reason says "reconnect": expired, refused, no Gmail scope)
  can't come in by waiting: it isn't waited for, and the Brief names it
  "reconnect" (honest(), mail_unread_by) rather than late. With none
  connected, Mail.app's headers did. Mail that becomes unavailable is in.
- messages: the app's chat.db read (imessage_tools.cache_messages) arrived
  after the gap ended, readable or not.
- day: Day plans from calendar and reminders, so it is in when both are.
- drafts: a proactive cycle (cards and drafts) finished after the drafts'
  target, on the MODEL ("model_as_of"), not a rules-only light cycle. Only when
  the Brief's pacing has no model build to give at open (brief_pace says
  "light": the model read the Brief within the day and no build time is due)
  does a light cycle on the fresh sources count, as the pacing intends. While
  a cycle runs, they are not in yet. A source that went silent and left the
  wait no longer holds the drafts back.

The Brief's own automatic cycle waits too, until the drafts' target is fixed
(mail and texts in, or silent): a model build on last night's caches would use
up the 15-minute build gap, and the build on the fresh sources could not run.

Priority: while a window is open, scheduled background model work waits, through
the gate it already obeys (Demo mode's background hold: behavior learning and
its use-log/writing-profile passes, the behavior phraser, memory capture, mail
memory, the attachment index). The Brief's own work (the cycle, its catch-up,
the gists) does not ask that gate's background half. A live turn still beats
everything: the cycle and the catch-up stand down for it as they always have.
The hold ends when the window closes, at the latest after GIVE_UP_S.
"""
from __future__ import annotations

import time

LONG_GAP_S = 6 * 3600
SLEEP_GAP_S = 3 * 3600
GIVE_UP_S = 3 * 60
SILENT_S = 90
LATE_FOR_S = 30 * 60
# The catch-up's settle wait inside a window (return_refresh.SETTLE_S is 45 s).
SETTLE_S = 20
# The scheduler's tick (scheduler.TICK_S): a wake's reads count from two ticks after the last one.
TICK_S = 30.0
# While building, the cycle may skip its usual gap at most this often.
WARM_EVERY_S = 120
# Re-evaluate at most this often (the background hold is asked by several loops).
_CACHE_S = 2.0

SOURCES = ("email", "messages", "calendar", "reminders", "day", "drafts")
READ_SOURCES = ("email", "messages", "calendar", "reminders")
LABELS = {"email": "Mail", "messages": "Messages", "calendar": "Calendar",
          "reminders": "Reminders", "day": "Day", "drafts": "Drafts"}

# {"reason", "started" (wall), "deadline_mono", "opened_mono", "wait" (set),
#  "after" (drafts target or None),
#  "silent" (sources that left the wait), "warm_at" (mono)}
_window: dict | None = None
_late: dict | None = None        # {"sources": [...], "until_mono", "window"} after a timeout
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


def _cycle_paused() -> bool:
    """The automatic cycle doesn't run (a remote model without parity): no drafts can come in."""
    try:
        from service.proactive import cycle
        return bool(cycle._remote_background_paused())
    except Exception:  # noqa: BLE001 — unknown: as if it runs
        return False


# --- what a source says now -----------------------------------------------------
# Each reader returns (state, stamp): state is "in", "waiting" or "terminal"
# (off, no permission, unavailable: never waited for); stamp is when it read.


def _native(source: str, started: float, now: float, status=None) -> tuple[str, float | None]:
    """Calendar or Reminders from the app's sync report."""
    if status is None:
        from service.assistant import scheduler
        status = scheduler.connectors_status()
    raw = status.get(source) or {}
    stamp = _stamp(raw.get("last_sync"))
    if stamp is None or stamp < started:
        return "waiting", stamp
    if raw.get("syncing"):
        return "waiting", stamp
    return ("in" if raw.get("available") else "terminal"), stamp


def _email(started: float, now: float) -> tuple[str, float | None]:
    from service.tools import email_tools as mail
    if mail.email_sync_state() == "unavailable":
        return "terminal", None
    accounts = dict(getattr(mail, "_external_accounts", {}) or {})
    if accounts:
        stamps, lagging, failing, erroring = [], False, 0, 0
        for address, item in accounts.items():
            stamp = _stamp((item or {}).get("headers_at"))
            if stamp is not None and stamp >= started:
                stamps.append(stamp)
                continue
            if _needs_reconnect(str(address), item or {}):
                # Its sign-in stops mail (expired, refused, no Gmail scope): it can't
                # come in by waiting, and the Brief names it "reconnect" (honest(),
                # mail_unread_by). Not waited for, and not counted as read.
                failing += 1
                continue
            error_at = _stamp((item or {}).get("headers_error_at"))
            if (item or {}).get("headers_error") and error_at is not None and error_at >= started:
                # It failed for now (offline, timed out) since the gap ended. An
                # error is never a read.
                erroring += 1
                continue
            # No fetch since the gap ended yet, or only an error from before it
            # (an error is kept, and restored at launch, until a fetch succeeds):
            # its fetch after the wake may be in flight, so it is waited for.
            lagging = True
        if erroring and not stamps and not lagging:
            # No account has read since the gap ended and the rest failed for now:
            # it may come in any minute, so wait (named late if it stays silent).
            # Never "in" on last night's mail.
            return "waiting", None
        # Every account has read or failed: only the healthy ones are waited on;
        # one failing for now doesn't hold the Brief (honest() names it).
        if not stamps and not lagging and failing:
            return "terminal", None  # every account needs reconnecting: named "reconnect", not waited for
        # One account read and another hasn't yet: waiting, but reporting (not silent).
        return ("waiting" if lagging else "in"), max(stamps) if stamps else None
    if getattr(mail, "_email_sync_pending", False):
        return "waiting", None
    stamp = _stamp(getattr(mail, "_native_headers_at", None))
    return ("in" if stamp is not None and stamp >= started else "waiting"), stamp


def _needs_reconnect(address: str, item: dict) -> bool:
    """The account's failure is one only the person can fix (cycle._unread_reason
    says "reconnect"); a temporary one (offline, a timeout) is not."""
    error = str(item.get("headers_error") or "")
    if not error and not item.get("auth_expired"):
        return False
    try:
        from service.proactive import cycle
        return cycle._unread_reason(address, item, str(item.get("error") or ""), error) == "reconnect"
    except Exception:  # noqa: BLE001 — unknown: wait for it (silence ends the wait)
        return False


def _messages(started: float, now: float) -> tuple[str, float | None]:
    from service.tools import imessage_tools as messages
    stamp = _stamp(getattr(messages, "_snapshot_captured_at", None))
    if not getattr(messages, "_sync_completed", False) or stamp is None or stamp < started:
        return "waiting", stamp
    return "in", stamp


def _drafts_in(after: float, state: dict, key: str = "model_as_of") -> bool:
    """A cycle finished after ``after``: on the model ("model_as_of"), or any
    cycle ("as_of") when the pacing gives no model build now (_drafts_key)."""
    try:
        from service.proactive import cycle
        if cycle.running():
            return False
    except Exception:  # noqa: BLE001
        pass
    stamp = _stamp(state.get(key))
    return stamp is not None and stamp >= after


def _drafts_key(now: float, state: dict) -> str:
    """Which cycle makes the drafts current: a model one, unless the Brief's
    pacing says "light" (no model build is owed now, by design)."""
    try:
        from service import brief_pace
        return "as_of" if brief_pace.automatic_plan(now, state) == "light" else "model_as_of"
    except Exception:  # noqa: BLE001 — unknown: the model's
        return "model_as_of"


def _read(source: str, window: dict, now: float, readers: dict) -> tuple[str, float | None]:
    started = window["started"]
    try:
        if source in readers:
            return readers[source](started, now)
        if source == "email":
            return _email(started, now)
        if source == "messages":
            return _messages(started, now)
        return _native(source, started, now)
    except Exception:  # noqa: BLE001 — unknown: not in yet (silence or the timeout ends the wait)
        return "waiting", None


# --- opening ---------------------------------------------------------------------


def begin(reason: str, *, now: float | None = None, readers=None, read_from: float | None = None,
          store=None, fresh: bool = False) -> bool:
    """Open a window, once for this launch or wake. One already open is kept,
    unless ``fresh`` (a wake: the Mac slept through it, so its reads and its
    deadline are from before the gap and a new one replaces it). Reads count
    from ``read_from`` (when the gap ended; default now). The wait set leaves
    out what is terminal now. True when one is open."""
    global _window, _late, _cache
    now = time.time() if now is None else now
    read_from = now if read_from is None or not read_from <= now else float(read_from)
    if _window is not None and not fresh:
        return True
    if _window is not None:
        _log(f"a {reason} during the open window ({_window['reason']}): building again")
        _window, _cache = None, None
    if _cycle_paused():
        _log(f"not building ({reason}): the automatic cycle is paused")
        return False
    window = {"reason": reason, "started": read_from, "opened_mono": time.monotonic(),
              "deadline_mono": time.monotonic() + GIVE_UP_S, "after": None,
              "silent": set(), "warm_at": None,
              "drafts_key": _drafts_key(now, _store_state(store))}
    readers = readers or {}
    wait = {s for s in READ_SOURCES if _read(s, window, now, readers)[0] != "terminal"}
    window["wait"] = wait | {"drafts"} | ({"day"} if wait & {"calendar", "reminders"} else set())
    _window, _late, _cache = window, None, None
    _log(f"building ({reason}): waiting for " + ", ".join(s for s in SOURCES if s in window["wait"]))
    return True


def tick(*, now: float | None = None, store=None) -> None:
    """Each scheduler tick: the first one after the service started (a launch
    after LONG_GAP_S) and a wake (a wall-clock jump of SLEEP_GAP_S since the
    last tick, on a Brief that was built). Nothing else opens a window. Never raises."""
    global _last_tick, _launch_checked
    try:
        now = time.time() if now is None else now
        previous, _last_tick = _last_tick, now
        if not _launch_checked:
            _launch_checked = True
            if long_gap(now, _store_state(store)):
                try:
                    from service.assistant import scheduler
                    started = float(scheduler.STARTED_AT)
                except Exception:  # noqa: BLE001
                    started = now
                begin("launch", now=now, read_from=min(now, started), store=store)
        elif previous is not None and now - previous >= SLEEP_GAP_S \
                and _stamp(_store_state(store).get("as_of")) is not None:
            # Asleep until at least two ticks after the last one: reads from then count.
            # A window still open from before the sleep is replaced (begin's fresh).
            begin("wake", now=now, read_from=min(now, previous + 2 * TICK_S), store=store, fresh=True)
        _evaluate(now, store)
    except Exception:  # noqa: BLE001 — the Brief shows as it did before
        pass


# --- the sources ---------------------------------------------------------------


def _waiting(window: dict, now: float, *, store=None, readers=None, mono: float | None = None) -> list[str]:
    """The sources in this window's wait set not in yet, in SOURCES order.
    A read source silent for SILENT_S moves to window["silent"] and stops waiting."""
    readers = readers or {}
    mono = time.monotonic() if mono is None else mono
    state = _store_state(store)
    result = {s: _read(s, window, now, readers) for s in READ_SOURCES if s in window["wait"]}
    quiet = mono - window["opened_mono"] >= SILENT_S
    waiting = []
    for source, (said, _stamp_) in result.items():
        if said in ("in", "terminal") or (_stamp_ is not None and _stamp_ >= window["started"]):
            window["silent"].discard(source)         # reporting again: waiting, not late
        if said in ("in", "terminal"):
            continue
        if quiet and (_stamp_ is None or _stamp_ < window["started"]):
            window["silent"].add(source)      # never reported since it opened: named late, not waited for
            continue
        waiting.append(source)
    if "day" in window["wait"] and ({"calendar", "reminders"} & set(waiting)):
        waiting.append("day")
    if window["after"] is None and not ({"email", "messages"} & set(waiting)):
        stamps = [stamp for s, (said, stamp) in result.items()
                  if s in ("email", "messages") and said == "in" and stamp]
        window["after"] = max([window["started"]] + stamps)   # fixed from here on
    if "drafts" in window["wait"]:
        if window["after"] is None:
            waiting.append("drafts")
        else:
            try:
                ok = (readers["drafts"](window["after"], now) if "drafts" in readers
                      else _drafts_in(window["after"], state, window.get("drafts_key", "model_as_of")))
            except Exception:  # noqa: BLE001
                ok = False
            if not ok:
                waiting.append("drafts")
    return [s for s in SOURCES if s in waiting]


def pending(now: float | None = None, *, store=None, readers=None) -> list[str]:
    """The sources not in yet. [] with no window open."""
    if _window is None:
        return []
    return _waiting(_window, time.time() if now is None else now, store=store, readers=readers)


def _silent_late(window: dict) -> list[str]:
    silent = set(window.get("silent") or ())
    if silent & {"calendar", "reminders"} and "day" in window["wait"]:
        silent.add("day")
    return [s for s in SOURCES if s in silent]


def _evaluate(now: float, store=None, readers=None, mono: float | None = None) -> dict:
    """Close the window when everything is in or time is up; the view."""
    global _window, _late, _cache
    mono = time.monotonic() if mono is None else mono
    if readers is None and _cache is not None and 0 <= mono - _cache[0] < _CACHE_S:
        return _cache[1]
    view = {"building": False, "building_for": [], "late": []}
    if _window is not None:
        waiting = _waiting(_window, now, store=store, readers=readers, mono=mono)
        silent = _silent_late(_window)
        if not waiting:
            _log(f"complete ({_window['reason']}, {mono - _window['opened_mono']:.0f} s)"
                 + (" without " + ", ".join(silent) if silent else ""))
            if silent:
                _late = {"sources": silent, "until_mono": mono + LATE_FOR_S, "window": _window}
            _window = None
        elif mono >= _window["deadline_mono"]:
            late = [s for s in SOURCES if s in set(waiting) | set(silent)]
            _log("gave up waiting for " + ", ".join(late))
            _late = {"sources": late, "until_mono": mono + LATE_FOR_S, "window": _window}
            _window = None
        else:
            view.update(building=True, building_for=waiting, late=silent)
    if _window is None and _late is not None:
        if mono >= _late["until_mono"]:
            _late = None
        else:
            # A late source drops off once it comes in. The drafts stay late
            # while a source they wait for does.
            window = _late["window"]
            window["silent"] = set()
            still = [s for s in _waiting(window, now, store=store, readers=readers, mono=window["opened_mono"])
                     if s in _late["sources"]]
            _late["sources"] = still
            if not still:
                _late = None
            view["late"] = list(still)
    _cache = (mono, view)
    return view


def view(now: float | None = None, *, store=None) -> dict:
    """What workspace.proactive carries: ``building`` (the window is open),
    ``building_for`` (source ids not in yet) and ``late`` (sources that didn't
    come in: silent ones while building, everything missing after a timeout,
    until each comes in). Never raises."""
    try:
        return dict(_evaluate(time.time() if now is None else now, store))
    except Exception:  # noqa: BLE001
        return {"building": False, "building_for": [], "late": []}


def building(now: float | None = None) -> bool:
    return view(now)["building"]


def defers_cycle(now: float | None = None) -> bool:
    """The automatic cycle (build, catch-up or tick) waits: the window is open
    and its mail and texts aren't in yet, so a cycle now would read last night's
    caches and use up the build gap the drafts need. Ends when they are in, go
    silent (SILENT_S) or the window closes (GIVE_UP_S)."""
    if not building(now):
        return False
    window = _window
    return window is not None and "drafts" in window["wait"] and window["after"] is None


def wants_cycle(now: float | None = None, mono: float | None = None) -> bool:
    """Mail and messages are in and the drafts aren't: the next cycle tick may
    run before its usual gap, at most once per WARM_EVERY_S (the chat and turn
    rules still hold). Asking doesn't use the slot: spend_warm() does, once the
    cycle's other checks passed and it starts."""
    v = view(now)
    waiting = v["building_for"]
    if not (v["building"] and "drafts" in waiting and "email" not in waiting and "messages" not in waiting):
        return False
    window = _window
    if window is None:
        return False
    mono = time.monotonic() if mono is None else mono
    return window["warm_at"] is None or mono - window["warm_at"] >= WARM_EVERY_S


def spend_warm(mono: float | None = None) -> None:
    """The cycle started on wants_cycle's early slot: the next is WARM_EVERY_S away."""
    window = _window
    if window is not None:
        window["warm_at"] = time.monotonic() if mono is None else mono


def holds_scheduled_work(now: float | None = None) -> bool:
    """Scheduled background model work waits while the Brief builds after a long gap."""
    return building(now)


def settle_s(default: float) -> float:
    """The catch-up's settle wait: shorter while the Brief is building."""
    return min(default, SETTLE_S) if _window is not None else default


def status() -> dict:
    window = None
    if _window:
        window = {k: (sorted(v) if isinstance(v, set) else v) for k, v in _window.items()}
    return {"window": window, **view()}


def _reset_for_tests() -> None:
    global _window, _late, _last_tick, _launch_checked, _cache
    _window, _late, _last_tick, _launch_checked, _cache = None, None, None, False, None
