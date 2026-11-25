"""The Brief after a long gap (service/proactive/brief_warmup.py): it says it is
still building until every source it reads is in, gives up after GIVE_UP_S and
names what didn't come in, and holds other scheduled background model work
meanwhile (through the existing Demo-mode background gate). A turn still wins.

Invented data only: no live model, no native sources, no network, and no writes
outside a scratch HARBOR_HOME.
"""
import os
import tempfile
from pathlib import Path

_scratch = tempfile.TemporaryDirectory(prefix="harbor-brief-warmup-tests-")
os.environ.setdefault("HARBOR_HOME", _scratch.name)
os.environ.setdefault("MOE_DIR", _scratch.name)

import pytest

from service import stage_mode
from service.proactive import brief_warmup as W
from service.proactive import return_refresh as R
from service.proactive.store import ProactiveStore

ROOT = Path(__file__).resolve().parents[1]
NOW = 1_800_000_000.0          # 2027-01-15 08:00 UTC
HOUR = 3600.0


def _in(stamp=None):
    return lambda started, now: (True, stamp if stamp is not None else started + 1)


def _out(started, now):
    return (False, None)


def readers(**overrides):
    """Every source in by default; override one with _out to keep it waiting."""
    base = {"email": _in(), "messages": _in(), "calendar": _in(), "reminders": _in(),
            "drafts": lambda started, now: True}
    base.update(overrides)
    return base


@pytest.fixture
def store(tmp_path):
    W._reset_for_tests()
    R._reset_for_tests()
    stage_mode.set_active(False)
    s = ProactiveStore(tmp_path)
    s.save_state(as_of=NOW - 9 * HOUR, model_as_of=NOW - 9 * HOUR, timezone="UTC")
    yield s
    W._reset_for_tests()
    R._reset_for_tests()
    stage_mode.set_active(False)


# --- the threshold -------------------------------------------------------------------

def test_the_thresholds_are_written_down():
    assert W.LONG_GAP_S == 6 * HOUR and W.SLEEP_GAP_S == 3 * HOUR
    assert 2 * 60 <= W.GIVE_UP_S <= 3 * 60
    doc = W.__doc__
    assert "6 hours" in doc and "3 hours" in doc and "3 minutes" in doc


def test_a_long_gap_opens_a_window_and_a_short_one_does_not(store):
    assert W.consider("launch", now=NOW, store=store) is True
    assert W.view(NOW, store=store)["building"] is True or W._window is not None
    W._reset_for_tests()
    store.save_state(as_of=NOW - 2 * HOUR)
    assert W.consider("launch", now=NOW, store=store) is False
    assert W._window is None


def test_a_brief_that_was_never_built_is_the_first_brief_path(tmp_path):
    W._reset_for_tests()
    empty = ProactiveStore(tmp_path)
    assert W.consider("launch", now=NOW, store=empty) is False
    assert W._window is None


def test_the_first_tick_checks_the_launch_and_a_clock_jump_is_a_wake(store):
    W.tick(now=NOW - 2 * HOUR, store=store)            # launch, Brief 7 h old at that time
    assert W._window is not None and W._window["reason"] == "launch"
    W._reset_for_tests()
    store.save_state(as_of=NOW - 5 * 60)
    W.tick(now=NOW, store=store)                        # launch on a fresh Brief: nothing
    assert W._window is None
    W.tick(now=NOW + 30, store=store)                   # an ordinary tick
    assert W._window is None
    W.tick(now=NOW + 30 + 4 * HOUR, store=store)        # 4 h asleep between ticks
    assert W._window is not None and W._window["reason"] == "wake"


# --- still building ------------------------------------------------------------------

def test_building_until_every_source_is_in(store):
    W.begin("launch", now=NOW)
    v = W._evaluate(NOW + 5, store=store, readers=readers(email=_out, messages=_out))
    assert v["building"] is True
    # Mail and texts aren't in, so the drafts made from them can't be either.
    assert v["building_for"] == ["email", "messages", "drafts"]
    v = W._evaluate(NOW + 30, store=store, readers=readers(messages=_out))
    assert v["building_for"] == ["messages", "drafts"]
    v = W._evaluate(NOW + 60, store=store, readers=readers())
    assert v == {"building": False, "building_for": [], "late": []}
    assert W._window is None


def test_calendar_or_reminders_waiting_keeps_day_waiting(store):
    W.begin("wake", now=NOW)
    v = W._evaluate(NOW + 5, store=store, readers=readers(reminders=_out))
    assert v["building_for"] == ["reminders", "day"]


def test_drafts_wait_for_a_cycle_after_mail_and_texts(store):
    W.begin("launch", now=NOW)
    # Mail came in at +40 s; the last cycle finished at +20 s, before it.
    store.save_state(as_of=NOW + 20)
    v = W._evaluate(NOW + 45, store=store,
                    readers={"email": _in(NOW + 40), "messages": _in(NOW + 10),
                             "calendar": _in(), "reminders": _in()})
    assert v["building_for"] == ["drafts"]
    assert W.wants_cycle(NOW + 45) is True               # the cycle may run before its gap
    store.save_state(as_of=NOW + 50)
    W._cache = None
    v = W._evaluate(NOW + 55, store=store,
                    readers={"email": _in(NOW + 40), "messages": _in(NOW + 10),
                             "calendar": _in(), "reminders": _in()})
    assert v["building"] is False and W._window is None


def test_a_recent_read_from_before_the_window_counts():
    started, now = NOW, NOW + 10
    assert W._fresh(NOW - 5 * 60, started, now) is True          # a short quit
    assert W._fresh(NOW - 8 * HOUR, started, now) is False       # from before the night
    assert W._fresh(None, started, now) is False
    assert W._fresh(True, started, now) is False


def test_the_native_report_must_be_new_and_not_syncing():
    status = {"calendar": {"last_sync": NOW - 9 * HOUR, "syncing": False},
              "reminders": {"last_sync": NOW + 3, "syncing": True}}
    assert W._native("calendar", NOW, NOW + 5, status)[0] is False
    assert W._native("reminders", NOW, NOW + 5, status)[0] is False
    status["calendar"]["last_sync"] = NOW + 2
    assert W._native("calendar", NOW, NOW + 5, status)[0] is True


# --- the timeout ---------------------------------------------------------------------

def test_after_the_timeout_it_shows_what_is_there_and_names_the_rest(store):
    W.begin("launch", now=NOW)
    late = W.GIVE_UP_S + 1
    v = W._evaluate(NOW + late, store=store, readers=readers(messages=_out))
    assert v["building"] is False and W._window is None
    assert v["late"] == ["messages", "drafts"]
    # Messages comes in later: it drops off the late list, and so does the list.
    v = W._evaluate(NOW + late + 30, store=store, readers=readers())
    assert v["late"] == [] and W._late is None


def test_the_late_note_ends_on_its_own(store):
    W.begin("launch", now=NOW)
    W._evaluate(NOW + W.GIVE_UP_S + 1, store=store, readers=readers(email=_out))
    v = W._evaluate(NOW + W.GIVE_UP_S + W.LATE_FOR_S + 2, store=store, readers=readers(email=_out))
    assert v["late"] == [] and W._late is None


def test_view_never_raises(monkeypatch):
    W._reset_for_tests()
    W.begin("launch", now=NOW)

    def broken(*args, **kwargs):
        raise RuntimeError("invented")
    monkeypatch.setattr(W, "_waiting", broken)
    assert W.view(NOW + 1) == {"building": False, "building_for": [], "late": []}
    W._reset_for_tests()


def test_an_unreadable_source_waits_only_until_the_timeout(store, monkeypatch):
    def broken(started, now):
        raise RuntimeError("invented")
    W.begin("launch", now=NOW)
    assert "email" in W._evaluate(NOW + 1, store=store, readers=readers(email=broken))["building_for"]
    assert W._evaluate(NOW + W.GIVE_UP_S, store=store, readers=readers(email=broken))["late"][:1] == ["email"]


# --- priority ------------------------------------------------------------------------

def test_scheduled_background_work_waits_while_building_but_the_brief_does_not(store, monkeypatch):
    assert stage_mode.holds_background() is False
    monkeypatch.setattr(W, "holds_scheduled_work", lambda now=None: True)
    assert stage_mode.holds_background() is True                 # behavior, mail memory, capture…
    assert stage_mode.holds_background(brief_work=True) is False   # the Brief's own gists
    monkeypatch.setattr(W, "holds_scheduled_work", lambda now=None: False)
    assert stage_mode.holds_background() is False
    stage_mode.set_active(True)
    assert stage_mode.holds_background(brief_work=True) is True    # Demo mode still holds it all
    stage_mode.set_active(False)


def test_the_hold_cannot_outlast_the_timeout(store):
    W.begin("launch", now=NOW)
    W._evaluate(NOW + 1, store=store, readers=readers(email=_out))
    assert W._window is not None
    W._evaluate(NOW + W.GIVE_UP_S + 1, store=store, readers=readers(email=_out))
    W._cache = None
    assert W._window is None and W.holds_scheduled_work(NOW + W.GIVE_UP_S + 2) is False


def test_the_brief_goes_first_through_the_existing_gates():
    gists = (ROOT / "service" / "assistant" / "mail_gists.py").read_text(encoding="utf-8")
    assert "holds_background(brief_work=True)" in gists
    for name in ("service/mail_memory/runner.py", "service/memory/capture.py",
                 "service/assistant/behavior_phraser.py", "service/attachment_index.py",
                 "service/assistant/scheduler.py"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "holds_background()" in text, name                # held while the Brief builds
    cycle = (ROOT / "service" / "proactive" / "cycle.py").read_text(encoding="utf-8")
    assert "brief_warmup.tick()" in cycle and "wants_cycle()" in cycle
    # A turn still wins: the cycle keeps its chat and turn gates.
    assert 'if not brief_pace.may_start("cycle") or in_turn:' in cycle


def test_a_long_gap_return_opens_the_window_and_shortens_the_settle(store):
    assert R.arm("opened", now=NOW, store=store) is True
    assert W._window is not None and W._window["reason"] == "opened"
    assert W.settle_s(R.SETTLE_S) == W.SETTLE_S < R.SETTLE_S
    W._reset_for_tests()
    assert W.settle_s(R.SETTLE_S) == R.SETTLE_S


def test_a_short_return_does_not_open_a_window(store):
    store.save_state(as_of=NOW - HOUR, model_as_of=NOW - HOUR)
    R.arm("opened", now=NOW, store=store)
    assert W._window is None


# --- what the app gets ---------------------------------------------------------------

def test_the_snapshot_carries_the_building_fields(monkeypatch):
    from service.proactive import cycle
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
    assert "state.isWorking || state.building" in cards                  # follows it until it swaps in
    assert cards.count("if proactive.building { return BriefBuilding.emptyLine }") == 2
    assert "!proactive.building ? shown.todoLine" in cards
    view = (ROOT / "app" / "Sources" / "HarborApp" / "NotchBriefView.swift").read_text(encoding="utf-8")
    assert "ProcessingRow(label: BriefBuilding.label)" in view and "BriefBuilding.lateLine" in view


def test_this_file_is_in_the_simulation_manifest():
    manifest = (ROOT / "scripts" / "run_simulation_qa.py").read_text()
    assert '"tests/test_brief_warmup.py"' in manifest


def test_the_real_mail_and_messages_stamps(monkeypatch):
    from service.tools import email_tools as mail
    from service.tools import imessage_tools as messages
    monkeypatch.setattr(mail, "email_sync_state", lambda: "ready")
    monkeypatch.setattr(mail, "_external_accounts", {})
    monkeypatch.setattr(mail, "_email_sync_pending", False)
    monkeypatch.setattr(mail, "_native_headers_at", NOW - 8 * HOUR)       # Mail.app, before the night
    assert W._email(NOW, NOW + 5)[0] is False
    monkeypatch.setattr(mail, "_native_headers_at", NOW + 3)
    assert W._email(NOW, NOW + 5) == (True, NOW + 3)
    # Google accounts: each must have fetched since, or have failed (named elsewhere).
    monkeypatch.setattr(mail, "_external_accounts", {
        "sam@example.invalid": {"headers_at": NOW + 2},
        "lee@example.invalid": {"headers_at": NOW - 8 * HOUR}})
    assert W._email(NOW, NOW + 5)[0] is False
    monkeypatch.setattr(mail, "_external_accounts", {
        "sam@example.invalid": {"headers_at": NOW + 2},
        "lee@example.invalid": {"headers_at": NOW - 8 * HOUR, "headers_error": "invented"}})
    assert W._email(NOW, NOW + 5) == (True, NOW + 2)
    monkeypatch.setattr(mail, "email_sync_state", lambda: "unavailable")
    assert W._email(NOW, NOW + 5)[0] is True                             # terminal: honest() names it

    monkeypatch.setattr(messages, "_sync_completed", False)
    assert W._messages(NOW, NOW + 5)[0] is False
    monkeypatch.setattr(messages, "_sync_completed", True)
    monkeypatch.setattr(messages, "_snapshot_captured_at", NOW - 8 * HOUR)
    assert W._messages(NOW, NOW + 5)[0] is False
    monkeypatch.setattr(messages, "_snapshot_captured_at", NOW + 4)
    assert W._messages(NOW, NOW + 5) == (True, NOW + 4)
