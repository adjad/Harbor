"""Quit and relaunch speed: the ordering and bounds that keep Harbor's quit fast.

Service side (service/main.py): the app's event stream ends as soon as quitting
starts, so uvicorn's graceful wait (--timeout-graceful-shutdown 2) isn't spent on
it; every lifespan shutdown step is bounded so the engine stop runs inside the
app's 5 s wait. App side (AppDelegate.swift): Harbor answers quit Apple Events
itself, so AppKit's responder-chain check can't drop a Quit & Reopen; the bridge
and engine stop side by side; launch milestones are logged. Pure: no service
start, no app launch, no engine, no models.
"""
from __future__ import annotations

import asyncio
import re
import signal
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from service import main

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app" / "Sources" / "HarborApp"


def _src(name: str) -> str:
    return (APP / name).read_text()


def _body(text: str, signature: str) -> str:
    start = text.index(signature)
    open_at = text.index("{", start)
    depth = 0
    for i in range(open_at, len(text)):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0:
            return text[open_at:i + 1]
    raise AssertionError(signature)


# --- service: the event stream ends at quit -----------------------------------------

def test_a_waiting_stream_ends_promptly_when_quitting_starts():
    closed = []

    async def forever():
        try:
            yield "hello"
            await asyncio.sleep(3600)
            yield "never"
        finally:
            closed.append(True)

    async def run():
        got = []

        async def consume():
            async for item in main._until_quit(forever()):
                got.append(item)

        consumer = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        began = time.monotonic()
        main._end_event_streams()
        await asyncio.wait_for(consumer, 1.0)
        return got, time.monotonic() - began

    got, took = asyncio.run(run())
    assert got == ["hello"]
    assert took < 0.5, took
    assert closed == [True], "the wrapped generator's own cleanup (the hub's unsubscribe) runs"


def test_a_client_that_goes_away_mid_wait_closes_the_stream_at_once():
    """Crash, Force Quit or a dropped connection: the response task is cancelled while
    the wrapper waits for the next event. The pending step is cancelled first, so the
    hub's own cleanup (its unsubscribe) runs now and no RuntimeError comes out of aclose."""
    closed = []

    async def waiting():
        try:
            yield "hello"
            await asyncio.sleep(3600)
            yield "never"
        finally:
            closed.append(True)

    async def run():
        got = []

        async def consume():
            async for item in main._until_quit(waiting()):
                got.append(item)

        consumer = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        consumer.cancel()
        outcome = (await asyncio.gather(consumer, return_exceptions=True))[0]
        return got, outcome, list(closed)

    got, outcome, closed_now = asyncio.run(run())
    assert got == ["hello"]
    assert isinstance(outcome, asyncio.CancelledError), repr(outcome)
    assert closed_now == [True], "the wrapped generator is closed as the client goes, not later"


def test_a_stream_that_finishes_by_itself_is_passed_through_whole():
    async def three():
        for i in range(3):
            yield i

    async def run():
        return [item async for item in main._until_quit(three())]

    assert asyncio.run(run()) == [0, 1, 2]


def test_a_quit_on_one_loop_never_ends_a_later_loops_streams():
    """Tests (and nothing else) run several loops in one process."""
    asyncio.run(_set_quit())

    async def fresh():
        return main._quit_signal().is_set()

    assert asyncio.run(fresh()) is False


async def _set_quit():
    main._end_event_streams()
    assert main._quit_signal().is_set()


def test_shutdown_omlx_ends_the_event_stream_before_anything_else(monkeypatch):
    monkeypatch.setattr(main, "client", SimpleNamespace(managed=False), raising=False)

    async def run():
        assert await main.shutdown_omlx() == {"stopped": False}
        return main._quit_signal().is_set()

    assert asyncio.run(run()) is True
    body = Path(main.__file__).read_text()
    shutdown = body[body.index("async def shutdown_omlx()"):]
    assert shutdown.index("_end_event_streams()") < shutdown.index('if not getattr(client, "managed", True)')


def test_assistant_events_stream_is_wrapped_to_end_at_quit():
    text = Path(main.__file__).read_text()
    events = text[text.index("async def assistant_events()"):]
    events = events[:events.index("return StreamingResponse")]
    assert "_until_quit(assistant_hub.events(rotate_after=ASSISTANT_EVENTS_ROTATE_S))" in events


def test_sigterm_ends_the_streams_then_runs_the_handler_that_was_there():
    original = signal.getsignal(signal.SIGTERM)
    seen = []
    try:
        signal.signal(signal.SIGTERM, lambda signum, frame: seen.append(signum))

        async def run():
            main._chain_sigterm(asyncio.get_running_loop())
            signal.raise_signal(signal.SIGTERM)
            await asyncio.sleep(0.05)
            return main._quit_signal().is_set()

        assert asyncio.run(run()) is True
        assert seen == [signal.SIGTERM], "uvicorn's own handler still begins its shutdown"
    finally:
        signal.signal(signal.SIGTERM, original)


# --- service: bounded shutdown ----------------------------------------------------------

def test_bounded_cuts_a_task_that_ignores_cancellation():
    async def stubborn():
        ignored = 0
        while True:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                ignored += 1
                if ignored > 1:    # asyncio.run's own cleanup at the end of the test
                    raise

    async def run():
        began = time.monotonic()
        finished = await main._bounded(stubborn(), 0.1)
        return finished, time.monotonic() - began

    finished, took = asyncio.run(run())
    assert finished is False
    assert took < 0.5, took


def test_bounded_reports_a_finished_step_even_when_it_failed():
    async def broken():
        raise RuntimeError("fixture failure")

    async def quick():
        return 1

    assert asyncio.run(main._bounded(broken(), 1.0)) is True
    assert asyncio.run(main._bounded(quick(), 1.0)) is True


def test_every_step_before_the_engine_stop_fits_inside_the_apps_wait():
    """BackendManager.stopAndWait SIGKILLs 5 s after SIGTERM. With the event stream
    ended, uvicorn's graceful wait is about its own 0.1 s; the bounded steps after it
    must leave the engine stop at least a second."""
    before_engine = (main._SHUTDOWN_TRAINER_SECONDS + main._SHUTDOWN_TASKS_SECONDS
                     + 3 * main._SHUTDOWN_STEP_SECONDS)
    assert 0.1 + before_engine <= 4.0, before_engine
    text = Path(main.__file__).read_text()
    lifespan = text[text.index("async def lifespan(app: FastAPI):"):text.index("app = FastAPI(title=")]
    shutdown = lifespan[lifespan.index("    finally:"):]
    # The old unbounded awaits are gone from the shutdown.
    assert "await personal_style.stop()" not in shutdown.replace("            await personal_style.stop()", "")
    assert "await asyncio.gather(*started_tasks" not in shutdown
    assert "await mcp_manager.stop()" not in shutdown
    for name in ("style trainer", "background tasks", "summaries", "mcp", "client", "engine"):
        assert f'step("{name}"' in shutdown, name
    assert shutdown.index('step("client"') < shutdown.index('step("engine"')
    assert "_end_event_streams()" in shutdown
    assert "[lifecycle] stopped in" in shutdown


def test_startup_logs_its_timing_and_no_longer_sweeps_the_cache_before_serving():
    text = Path(main.__file__).read_text()
    lifespan = text[text.index("async def lifespan(app: FastAPI):"):text.index("app = FastAPI(title=")]
    before_yield = lifespan[:lifespan.index("        yield\n")]
    assert "omlx_cache.maintain()" not in before_yield
    assert "asyncio.to_thread(omlx_cache.maintain)" in before_yield
    assert "cache_sweep_task = asyncio.create_task(_sweep_omlx_cache())" in before_yield
    assert "[lifecycle] serving:" in before_yield
    assert "_chain_sigterm(asyncio.get_running_loop())" in before_yield
    assert text.index("    _resume_or_exit()") < text.index("_IMPORT_STARTED = _lifecycle_time.monotonic()") < text.index(
        "from service.assistant.verified_reminders import")


# --- app: ordering ----------------------------------------------------------------------

def test_harbor_answers_quit_apple_events_itself_first_thing_at_launch():
    app = _src("AppDelegate.swift")
    finished = _body(app, "func applicationDidFinishLaunching(")
    assert finished.index("installQuitAppleEventHandler()") < finished.index("SingleInstance.shouldYield(")
    install = _body(app, "private func installQuitAppleEventHandler()")
    assert "setEventHandler(" in install and "kAEQuitApplication" in install and "kCoreEventClass" in install
    handler = _body(app, "@objc private func handleQuitAppleEvent(")
    # Answered at once; the quit runs on the next turn, through applicationShouldTerminate.
    assert "DispatchQueue.main.async { NSApp.terminate(nil) }" in handler
    assert handler.index("PrivacyRelaunch.handledQuitEvent = event") < handler.index("NSApp.terminate(nil)")
    for blocking in ("waitUntilExit", "Thread.sleep", "usleep(", "await "):
        assert blocking not in handler, blocking
    # The sender is still read from the event (PrivacyRelaunch.Quit.current()).
    current = _body(_src("PrivacyRelaunch.swift"), "static func current()")
    assert "PrivacyRelaunch.handledQuitEvent" in current and "currentAppleEvent" in current


def test_the_bridge_and_engine_stop_side_by_side_before_the_service():
    shutdown = _body(_src("AppDelegate.swift"), "private func beginShutdown()")
    bridge = shutdown.index("async let bridgeStopped: Void = BrowserBridgeActivation.shared.shutdown()")
    engine = shutdown.index("await client.shutdownOMLX()")
    assert shutdown.index("await PendingConfigWrites.shared.waitUntilIdle()") < bridge < engine
    assert engine < shutdown.index("await bridgeStopped") < shutdown.index("await backend.stopAndWait()")
    assert "BrowserBridgeActivation.shared.shutdown()" not in shutdown.replace(
        "async let bridgeStopped: Void = BrowserBridgeActivation.shared.shutdown()", "")
    assert "asyncAfter(deadline: .now() + 20)" in shutdown, "the hard bound stays"


def test_quit_and_launch_are_timed_in_the_log():
    app = _src("AppDelegate.swift")
    assert "LaunchTimeline.quitLine(" in _body(app, "private func finishShutdown()")
    launch = _body(app, "private func launch()")
    present = launch.index("panel?.present()")
    assert present < launch.index("LaunchTimeline.mark(.notchShown)")
    assert "DispatchQueue.main.async { LaunchTimeline.mark(.notchShown) }" in launch
    for mark in (".finishedLaunching", ".serviceStarted", ".serviceReady", ".dayLoaded"):
        assert f"LaunchTimeline.mark({mark})" in launch, mark


def test_a_reopen_comes_back_to_the_harbor_window_that_was_up():
    app = _src("AppDelegate.swift")
    should = _body(app, "func applicationShouldTerminate(")
    assert should.index("relaunchIfSystemSettingsAsked(PrivacyRelaunch.Quit.current())") < should.index(
        "PrivacyRelaunch.noteWindowForReopen(openHarborWindowForReopen())") < should.index("beginShutdown()")
    relaunch = _body(app, "private func relaunchIfSystemSettingsAsked(")
    assert relaunch.index("try helper.run()") < relaunch.index("PrivacyRelaunch.noteReopenScheduled()")
    probe = _body(_src("AccessProbeMonitor.swift"), "func reopenHarbor()")
    assert probe.index("try helper.run()") < probe.index("PrivacyRelaunch.noteReopenScheduled()") < probe.index(
        "NSApp.terminate(nil)")
    launch = _body(app, "private func launch()")
    assert launch.index("NotchPermissionPage.takeResume()") < launch.index("PrivacyRelaunch.takeReopenWindow()")
    assert re.search(r"if let window = PrivacyRelaunch\.takeReopenWindow\(\), !firstRun", launch)
