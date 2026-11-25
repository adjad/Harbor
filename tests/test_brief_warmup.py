"""The Brief after a long gap (service/proactive/brief_warmup.py): it says it is
still building until every source it reads is in, gives up after GIVE_UP_S and
names what didn't come in, and holds other scheduled background model work
meanwhile (through the existing Demo-mode background gate). A turn still wins.

A window opens once per launch or wake, never on a notch open, and never when the
automatic cycle is paused. The wait set and the drafts' target are fixed, a stale
mail error is not a read, and a silent source leaves the wait after SILENT_S.

Invented data only: no live model, no native sources, no network, and no writes
outside a scratch HARBOR_HOME.
"""
import os
import tempfile
import time
from pathlib import Path

_scratch = tempfile.TemporaryDirectory(prefix="harbor-brief-warmup-tests-")
os.environ.setdefault("HARBOR_HOME", _scratch.name)
os.environ.setdefault("MOE_DIR", _scratch.name)

import pytest

from service import stage_mode
from service.proactive import brief_warmup as W
from service.proactive import cycle
from service.proactive import return_refresh as R
from service.proactive.store import ProactiveStore

ROOT = Path(__file__).resolve().parents[1]
NOW = 1_800_000_000.0          # 2027-01-15 08:00 UTC
HOUR = 3600.0


def came(stamp=None):
    """A source that read after the window opened (at ``stamp``, default started + 1)."""
    return lambda started, now: ("in", stamp if stamp is not None else started + 1)


def waiting(started, now):
    return ("waiting", None)


def terminal(started, now):
    return ("terminal", None)


def reporting(started, now):
    """Mid-read: it reported since the window opened but isn't done."""
    return ("waiting", started + 1)


def readers(**overrides):
    base = {"email": came(), "messages": came(), "calendar": came(), "reminders": came(),
            "drafts": lambda after, now: True}
    base.update(overrides)
    return base


def opened(reason="launch", *, at=NOW, **overrides):
    """Open a window at ``at`` whose wait set is read through ``readers``."""
    W.begin(reason, now=at, readers=readers(**overrides))
    return W._window


def evaluate(at, *, after_open=0.0, store=None, **overrides):
    """The view at wall time ``at``, ``after_open`` monotonic seconds after the window opened."""
    window = W._window or (W._late or {}).get("window")
    mono = (window["opened_mono"] if window else time.monotonic()) + after_open
    return W._evaluate(at, store=store, readers=readers(**overrides), mono=mono)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    W._reset_for_tests()
    R._reset_for_tests()
    stage_mode.set_active(False)
    monkeypatch.setattr(cycle, "_remote_background_paused", lambda: False)
    yield
    W._reset_for_tests()
    R._reset_for_tests()
    stage_mode.set_active(False)


@pytest.fixture
def store(tmp_path):
    s = ProactiveStore(tmp_path)
    s.save_state(as_of=NOW - 9 * HOUR, model_as_of=NOW - 9 * HOUR, timezone="UTC")
    return s


# --- the thresholds ------------------------------------------------------------------

def test_the_thresholds_are_written_down():
    assert W.LONG_GAP_S == 6 * HOUR and W.SLEEP_GAP_S == 3 * HOUR
    assert 2 * 60 <= W.GIVE_UP_S <= 3 * 60 and W.SILENT_S < W.GIVE_UP_S
    doc = W.__doc__
    assert "6 hours" in doc and "3 hours" in doc and "3 minutes" in doc and "90 seconds" in doc


# --- once per gap (Auditor finding 1) ------------------------------------------------

def test_a_launch_after_a_long_gap_opens_one_window(store):
    W.tick(now=NOW, store=store)
    assert W._window is not None and W._window["reason"] == "launch"


def test_a_launch_on_a_recent_brief_or_a_new_install_opens_none(store, tmp_path):
    store.save_state(as_of=NOW - 2 * HOUR)
    W.tick(now=NOW, store=store)
    assert W._window is None
    W._reset_for_tests()
    W.tick(now=NOW, store=ProactiveStore(tmp_path / "never-built"))
    assert W._window is None


def test_after_it_times_out_nothing_reopens_it_until_the_next_wake(store):
    W.tick(now=NOW, store=store)                                   # launch, 9 h gap
    W._evaluate(NOW + 200, store=store, readers=readers(drafts=lambda a, n: False),
                mono=W._window["opened_mono"] + W.GIVE_UP_S + 1)
    assert W._window is None                                       # timed out; as_of never moved
    for minute in range(1, 30):                                    # ticks, notch opens, unparks
        W.tick(now=NOW + 200 + 30 * minute, store=store)
        R.arm("opened", now=NOW + 200 + 30 * minute, store=store)
        R.arm("unparked", now=NOW + 200 + 30 * minute, store=store)
        assert W._window is None
    W.tick(now=NOW + 200 + 30 * 30 + 4 * HOUR, store=store)        # a real sleep and wake
    assert W._window is not None and W._window["reason"] == "wake"


def test_reads_right_after_a_wake_count_even_before_the_window_opens(store):
    W.tick(now=NOW, store=store)                          # launch
    W._reset_for_tests(); W._launch_checked = True
    W._last_tick = NOW
    wake = NOW + 9 * HOUR                                 # the next tick, after a night asleep
    W.tick(now=wake, store=store)
    window = W._window
    assert window["reason"] == "wake" and window["started"] == NOW + 2 * W.TICK_S
    # Every source read a few seconds after the Mac woke, before this tick.
    v = evaluate(wake + 1, after_open=W.SILENT_S + 1, store=store,
                 email=came(wake - 5), messages=came(wake - 4), calendar=came(wake - 3), reminders=came(wake - 2))
    assert v == {"building": False, "building_for": [], "late": []}


def test_a_launch_counts_reads_from_the_service_start(store, monkeypatch):
    from service.assistant import scheduler
    monkeypatch.setattr(scheduler, "STARTED_AT", NOW - 20)
    W.tick(now=NOW, store=store)
    assert W._window["started"] == NOW - 20
    W._reset_for_tests()
    monkeypatch.setattr(scheduler, "STARTED_AT", NOW + 99)  # a bad clock: never later than now
    W.tick(now=NOW, store=store)
    assert W._window["started"] == NOW


def test_after_it_completes_nothing_reopens_it(store):
    W.tick(now=NOW, store=store)
    assert evaluate(NOW + 20, store=store)["building"] is False and W._window is None
    W.tick(now=NOW + 50, store=store)
    R.arm("opened", now=NOW + 60, store=store)
    assert W._window is None


def test_a_notch_open_or_unpark_never_opens_one(store):
    W._launch_checked = True                       # the launch tick already ran on a fresh Brief
    R.arm("opened", now=NOW, store=store)
    R.arm("unparked", now=NOW, store=store)
    assert W._window is None


def test_no_window_while_the_automatic_cycle_is_paused(store, monkeypatch):
    monkeypatch.setattr(cycle, "_remote_background_paused", lambda: True)
    W.tick(now=NOW, store=store)
    assert W._window is None
    assert W.begin("wake", now=NOW) is False and W.holds_scheduled_work() is False


def test_the_hold_ends_with_the_timeout(store):
    opened(email=waiting)
    assert evaluate(NOW + 5, store=store, email=reporting)["building"] is True
    evaluate(NOW + 200, after_open=W.GIVE_UP_S + 1, store=store, email=reporting)
    W._cache = None
    assert W._window is None and W.holds_scheduled_work() is False


def test_the_deadline_is_monotonic_so_a_clock_change_cannot_stretch_it(store):
    opened(email=waiting)
    # The wall clock jumps back an hour after the wake; monotonic time keeps going.
    v = evaluate(NOW - HOUR, after_open=W.GIVE_UP_S + 1, store=store, email=reporting)
    assert v["building"] is False and "email" in v["late"]


# --- still building ------------------------------------------------------------------

def test_building_until_every_source_is_in(store):
    opened(email=waiting, messages=waiting)
    v = evaluate(NOW + 5, store=store, email=reporting, messages=reporting)
    assert v["building"] is True and v["building_for"] == ["email", "messages", "drafts"]
    assert evaluate(NOW + 30, after_open=3, store=store, messages=reporting)["building_for"] == ["messages", "drafts"]
    assert evaluate(NOW + 60, after_open=6, store=store) == {"building": False, "building_for": [], "late": []}
    assert W._window is None


def test_calendar_or_reminders_waiting_keeps_day_waiting(store):
    opened()
    assert evaluate(NOW + 5, store=store, reminders=reporting)["building_for"] == ["reminders", "day"]


# --- the wait set is fixed (Auditor note: never-reporting sources) ----------------------

def test_a_source_terminal_at_open_is_never_waited_for(store):
    window = opened(messages=terminal, calendar=terminal)
    assert window["wait"] == {"email", "reminders", "day", "drafts"}
    v = evaluate(NOW + 5, store=store, messages=waiting, calendar=waiting, reminders=reporting)
    assert v["building_for"] == ["reminders", "day"]


def test_a_silent_source_leaves_the_wait_and_is_named_late(store):
    opened()
    v = evaluate(NOW + 10, after_open=10, store=store, messages=waiting)
    assert v["building"] is True and "messages" in v["building_for"]      # still within SILENT_S
    v = evaluate(NOW + 100, after_open=W.SILENT_S + 1, store=store, messages=waiting)
    # It never reported, so it no longer holds the Brief (or the drafts) back.
    assert v["building"] is False and v["late"] == ["messages"] and W._window is None
    # It comes in later: it drops off the late list.
    v = evaluate(NOW + 130, after_open=W.SILENT_S + 31, store=store)
    assert v["late"] == []


def test_a_silent_source_that_reports_again_is_waiting_not_late(store):
    opened()
    evaluate(NOW + 100, after_open=W.SILENT_S + 1, store=store, messages=waiting, email=reporting)
    assert W._window["silent"] == {"messages"}
    v = evaluate(NOW + 110, after_open=W.SILENT_S + 11, store=store, messages=reporting, email=reporting)
    assert "messages" in v["building_for"] and "messages" not in v["late"]


def test_one_mail_account_read_and_one_lagging_is_reporting(monkeypatch):
    from service.tools import email_tools as mail
    monkeypatch.setattr(mail, "email_sync_state", lambda: "ready")
    monkeypatch.setattr(mail, "_external_accounts", {
        "sam@example.invalid": {"headers_at": NOW + 2},
        "lee@example.invalid": {"headers_at": NOW - 8 * HOUR}})
    assert W._email(NOW, NOW + 5) == ("waiting", NOW + 2)


def test_a_source_that_is_reporting_is_not_silent(store):
    opened()
    v = evaluate(NOW + 100, after_open=W.SILENT_S + 1, store=store, email=reporting)
    assert v["building"] is True and "email" in v["building_for"]


# --- the drafts' target is fixed (Auditor finding 2) ---------------------------------

def test_a_reread_after_the_cycle_does_not_send_the_drafts_back(store):
    opened()
    # Mail at +40, texts at +10: the target is fixed at +40.
    v = evaluate(NOW + 45, store=store, email=came(NOW + 40), messages=came(NOW + 10),
                 drafts=lambda after, now: False)
    assert v["building_for"] == ["drafts"] and W._window["after"] == NOW + 40
    store.save_state(as_of=NOW + 50, model_as_of=NOW + 50)              # a model cycle after the target
    # Mail re-reads at +55 (the notch opened, a 5-minute fetch): the target stays.
    v = evaluate(NOW + 60, after_open=1, store=store, email=came(NOW + 55), messages=came(NOW + 10),
                 drafts=lambda after, now: W._drafts_in(after, store.state()))
    assert v["building"] is False and W._window is None


def test_drafts_wait_while_a_cycle_runs(store, monkeypatch):
    store.save_state(as_of=NOW + 50, model_as_of=NOW + 50)
    assert W._drafts_in(NOW + 40, store.state()) is True
    monkeypatch.setattr(cycle, "running", lambda: True)
    assert W._drafts_in(NOW + 40, store.state()) is False


def test_a_light_cycle_is_not_the_drafts_unless_the_pacing_gives_no_build(store, monkeypatch):
    from service import brief_pace
    # A rules-only light cycle after the target: as_of moves, model_as_of doesn't.
    store.save_state(as_of=NOW + 50, model_as_of=NOW - 9 * HOUR)
    assert W._drafts_in(NOW + 40, store.state()) is False
    monkeypatch.setattr(brief_pace, "automatic_plan", lambda now=None, state=None: "as_is")
    assert W._drafts_key(NOW, store.state()) == "model_as_of"
    monkeypatch.setattr(brief_pace, "automatic_plan", lambda now=None, state=None: "build")
    assert W._drafts_key(NOW, store.state()) == "model_as_of"
    # By day with the model's read under a day old and no build due, no model
    # build is owed by design: a light cycle on the fresh sources is the Brief.
    monkeypatch.setattr(brief_pace, "automatic_plan", lambda now=None, state=None: "light")
    assert W._drafts_key(NOW, store.state()) == "as_of"
    assert W._drafts_in(NOW + 40, store.state(), "as_of") is True
    W.begin("wake", now=NOW, readers=readers(), store=store)
    assert W._window["drafts_key"] == "as_of"


def test_the_cycle_may_skip_its_gap_at_most_once_per_interval(store):
    opened()
    evaluate(NOW + 45, store=store, drafts=lambda after, now: False)
    start = W._window["opened_mono"]
    W._cache = (time.monotonic(), {"building": True, "building_for": ["drafts"], "late": []})
    assert W.wants_cycle(mono=start) is True
    assert W.wants_cycle(mono=start + 30) is False
    assert W.wants_cycle(mono=start + W.WARM_EVERY_S + 1) is True


# --- a stale mail error is not a read (Auditor finding 3) ---------------------------

def test_a_mail_error_is_never_a_read_and_only_reconnect_skips_the_wait(monkeypatch):
    from service.tools import email_tools as mail
    monkeypatch.setattr(mail, "email_sync_state", lambda: "ready")
    offline = "Google could not be reached; try again when online"        # gmail_source's own words
    timeout = "Google refresh timed out; showing the last available cache"
    # A temporary failure from before the gap (the lid closed off Wi-Fi): waited for.
    for reason in (offline, timeout):
        monkeypatch.setattr(mail, "_external_accounts", {
            "sam@example.invalid": {"headers_at": NOW - 9 * HOUR, "headers_error": reason}})
        assert W._email(NOW, NOW + 5) == ("waiting", None), reason
    # It recovers after the wake: in.
    monkeypatch.setattr(mail, "_external_accounts", {
        "sam@example.invalid": {"headers_at": NOW + 3, "headers_error": ""}})
    assert W._email(NOW, NOW + 5) == ("in", NOW + 3)
    # A sign-in that stops mail can't come in by waiting: not waited for, not a read.
    monkeypatch.setattr(mail, "_external_accounts", {
        "sam@example.invalid": {"headers_at": NOW - 9 * HOUR, "headers_error": "Sign-in expired (invented)",
                                "auth_expired": True}})
    assert W._email(NOW, NOW + 5) == ("terminal", None)
    # One expired, one healthy that read: in. One expired, one not read yet: waiting.
    monkeypatch.setattr(mail, "_external_accounts", {
        "sam@example.invalid": {"headers_at": NOW - 9 * HOUR, "auth_expired": True, "headers_error": "x"},
        "lee@example.invalid": {"headers_at": NOW + 2}})
    assert W._email(NOW, NOW + 5) == ("in", NOW + 2)
    monkeypatch.setattr(mail, "_external_accounts", {
        "sam@example.invalid": {"headers_at": NOW - 9 * HOUR, "auth_expired": True, "headers_error": "x"},
        "lee@example.invalid": {"headers_at": NOW - 9 * HOUR, "headers_error": offline}})
    assert W._email(NOW, NOW + 5)[0] == "waiting"


def test_an_offline_mail_account_at_wake_keeps_the_drafts_waiting(store, monkeypatch):
    """The reviewer's case: Gmail offline before the gap, Messages in at once. Mail
    must stay in the wait, so the drafts' target isn't fixed on last night's mail."""
    from service.tools import email_tools as mail
    monkeypatch.setattr(mail, "email_sync_state", lambda: "ready")
    monkeypatch.setattr(mail, "_external_accounts", {
        "sam@example.invalid": {"headers_at": NOW - 9 * HOUR,
                                "headers_error": "Google could not be reached; try again when online"}})
    W.begin("wake", now=NOW, readers={k: v for k, v in readers().items() if k != "email"})
    assert "email" in W._window["wait"]
    v = W._evaluate(NOW + 5, store=store, readers={k: v for k, v in readers().items() if k != "email"},
                    mono=W._window["opened_mono"] + 5)
    assert v["building_for"] == ["email", "drafts"] and W._window["after"] is None
    W._cache = (time.monotonic(), v)
    assert W.defers_cycle() is True


def test_the_brief_cycle_waits_for_fresh_mail_and_texts_before_its_model_build(store, monkeypatch):
    """The morning wake: a model build on last night's caches would use up the
    build gap, so the drafts could never come in. The cycle waits until mail and
    texts are in (or silent), then the one build runs on fresh sources."""
    monkeypatch.setattr(cycle, "running", lambda: False)
    monkeypatch.setattr(cycle, "_rerun", None)
    monkeypatch.setattr(W, "tick", lambda **kw: None)
    stood = []
    monkeypatch.setattr(cycle, "_stood_down", stood.append)
    started = []
    monkeypatch.setattr(cycle.asyncio, "create_task", lambda coro: (started.append(coro), coro.close())[0])
    opened(email=waiting, messages=waiting)
    W._cache = (time.monotonic(), {"building": True, "building_for": ["email", "messages", "drafts"], "late": []})
    assert W.defers_cycle() is True
    assert cycle.schedule_tick() is None and started == []          # no build on the caches
    assert stood == ["the Brief is waiting for fresh mail and texts"]
    # Mail and texts are in: the target is fixed, and the cycle may run before its gap.
    W._window["after"] = NOW + 60
    W._cache = (time.monotonic(), {"building": True, "building_for": ["drafts"], "late": []})
    assert W.defers_cycle() is False and W.wants_cycle() is True
    # Not deferred after the window closes either.
    W._reset_for_tests()
    assert W.defers_cycle() is False


def test_the_real_mail_and_messages_stamps(monkeypatch):
    from service.tools import email_tools as mail
    from service.tools import imessage_tools as messages
    monkeypatch.setattr(mail, "email_sync_state", lambda: "ready")
    monkeypatch.setattr(mail, "_external_accounts", {})
    monkeypatch.setattr(mail, "_email_sync_pending", False)
    monkeypatch.setattr(mail, "_native_headers_at", NOW - 5 * 60)          # before the window: not a read
    assert W._email(NOW, NOW + 5)[0] == "waiting"
    monkeypatch.setattr(mail, "_native_headers_at", NOW + 3)
    assert W._email(NOW, NOW + 5) == ("in", NOW + 3)
    monkeypatch.setattr(mail, "_external_accounts", {
        "sam@example.invalid": {"headers_at": NOW + 2},
        "lee@example.invalid": {"headers_at": NOW - 8 * HOUR}})
    assert W._email(NOW, NOW + 5)[0] == "waiting"
    monkeypatch.setattr(mail, "email_sync_state", lambda: "unavailable")
    assert W._email(NOW, NOW + 5)[0] == "terminal"

    monkeypatch.setattr(messages, "_sync_completed", False)
    assert W._messages(NOW, NOW + 5)[0] == "waiting"
    monkeypatch.setattr(messages, "_sync_completed", True)
    monkeypatch.setattr(messages, "_snapshot_captured_at", NOW - 8 * HOUR)
    assert W._messages(NOW, NOW + 5)[0] == "waiting"
    monkeypatch.setattr(messages, "_snapshot_captured_at", NOW + 4)
    assert W._messages(NOW, NOW + 5) == ("in", NOW + 4)


def test_the_native_report_must_come_after_the_window_opened():
    status = {"calendar": {"last_sync": NOW - 9 * HOUR, "syncing": False, "available": True},
              "reminders": {"last_sync": NOW + 3, "syncing": True}}
    assert W._native("calendar", NOW, NOW + 5, status)[0] == "waiting"
    assert W._native("reminders", NOW, NOW + 5, status) == ("waiting", NOW + 3)
    status["calendar"]["last_sync"] = NOW + 2
    assert W._native("calendar", NOW, NOW + 5, status)[0] == "in"
    status["calendar"]["available"] = False
    assert W._native("calendar", NOW, NOW + 5, status)[0] == "terminal"


# --- the timeout ---------------------------------------------------------------------

def test_after_the_timeout_it_shows_what_is_there_and_names_the_rest(store):
    opened()
    v = evaluate(NOW + 200, after_open=W.GIVE_UP_S + 1, store=store, messages=reporting)
    assert v["building"] is False and W._window is None
    assert v["late"] == ["messages", "drafts"]
    store.save_state(as_of=NOW + 250, model_as_of=NOW + 250)
    v = evaluate(NOW + 260, after_open=W.GIVE_UP_S + 30, store=store,
                 drafts=lambda after, now: W._drafts_in(after, store.state()))
    assert v["late"] == [] and W._late is None


def test_the_late_note_ends_on_its_own(store):
    opened()
    evaluate(NOW + 200, after_open=W.GIVE_UP_S + 1, store=store, email=reporting)
    v = evaluate(NOW + 3000, after_open=W.GIVE_UP_S + W.LATE_FOR_S + 2, store=store, email=reporting)
    assert v["late"] == [] and W._late is None


def test_view_never_raises(monkeypatch):
    opened()

    def broken(*args, **kwargs):
        raise RuntimeError("invented")
    monkeypatch.setattr(W, "_waiting", broken)
    assert W.view(NOW + 1) == {"building": False, "building_for": [], "late": []}


def test_an_unreadable_source_waits_only_until_it_is_silent(store):
    def broken(started, now):
        raise RuntimeError("invented")
    opened()
    assert "email" in evaluate(NOW + 1, store=store, email=broken)["building_for"]
    v = evaluate(NOW + 100, after_open=W.SILENT_S + 1, store=store, email=broken)
    assert v["building"] is False and v["late"][:1] == ["email"]


# --- priority ------------------------------------------------------------------------

def test_scheduled_background_work_waits_while_building_but_the_brief_does_not(monkeypatch):
    assert stage_mode.holds_background() is False
    monkeypatch.setattr(W, "holds_scheduled_work", lambda now=None: True)
    assert stage_mode.holds_background() is True                   # behavior, mail memory, capture…
    assert stage_mode.holds_background(brief_work=True) is False   # the Brief's own gists
    monkeypatch.setattr(W, "holds_scheduled_work", lambda now=None: False)
    assert stage_mode.holds_background() is False
    stage_mode.set_active(True)
    assert stage_mode.holds_background(brief_work=True) is True    # Demo mode still holds it all
    stage_mode.set_active(False)


def test_the_brief_goes_first_through_the_existing_gates():
    gists = (ROOT / "service" / "assistant" / "mail_gists.py").read_text(encoding="utf-8")
    assert "holds_background(brief_work=True)" in gists
    for name in ("service/mail_memory/runner.py", "service/memory/capture.py",
                 "service/assistant/behavior_phraser.py", "service/attachment_index.py",
                 "service/assistant/scheduler.py"):
        assert "holds_background()" in (ROOT / name).read_text(encoding="utf-8"), name
    source = (ROOT / "service" / "proactive" / "cycle.py").read_text(encoding="utf-8")
    assert "brief_warmup.tick()" in source and "wants_cycle()" in source
    assert 'if not brief_pace.may_start("cycle") or in_turn:' in source      # a turn still wins


def test_a_window_shortens_the_catch_ups_settle():
    assert W.settle_s(R.SETTLE_S) == R.SETTLE_S
    opened()
    assert W.settle_s(R.SETTLE_S) == W.SETTLE_S < R.SETTLE_S


# --- what the app gets ---------------------------------------------------------------

def test_the_snapshot_carries_the_building_fields(monkeypatch):
    monkeypatch.setattr(W, "view", lambda now=None, store=None: {"building": True,
                        "building_for": ["email", "drafts"], "late": []})
    assert cycle._building() == {"building": True, "building_for": ["email", "drafts"], "late": []}

    def broken(now=None, store=None):
        raise RuntimeError("invented")
    monkeypatch.setattr(W, "view", broken)
    assert cycle._building() == {"building": False, "building_for": [], "late": []}


def test_the_app_shows_building_and_never_an_all_clear_meanwhile():
    models = (ROOT / "app" / "Sources" / "HarborApp" / "ProactiveModels.swift").read_text(encoding="utf-8")
    assert "building_for" in models and '"Brief is still building…"' in models
    cards = (ROOT / "app" / "Sources" / "HarborApp" / "ProactiveCards.swift").read_text(encoding="utf-8")
    assert "state.isWorking || state.building" in cards
    assert cards.count("if proactive.building { return BriefBuilding.emptyLine }") == 2
    assert "!proactive.building ? shown.todoLine" in cards
    view = (ROOT / "app" / "Sources" / "HarborApp" / "NotchBriefView.swift").read_text(encoding="utf-8")
    assert "ProcessingRow(label: BriefBuilding.label)" in view and "BriefBuilding.lateLine" in view


def test_this_file_is_in_the_simulation_manifest():
    manifest = (ROOT / "scripts" / "run_simulation_qa.py").read_text()
    assert '"tests/test_brief_warmup.py"' in manifest
