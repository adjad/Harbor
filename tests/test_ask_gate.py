"""Brief's model calls wait for Ask (service/proactive/ask_gate.py).

Fake clocks and a fake engine only: no model is loaded and nothing is sent.
"""
import asyncio
import os
import tempfile

_scratch = tempfile.TemporaryDirectory(prefix="harbor-ask-gate-")
os.environ.setdefault("HARBOR_HOME", _scratch.name)

import pytest

from service import idle
from service.assistant import mail_gists as G
from service.proactive import ask_gate


@pytest.fixture(autouse=True)
def quiet_ask(monkeypatch):
    monkeypatch.setattr(idle, "_foreground_turns", 0)
    monkeypatch.setattr(idle, "_last_turn", None)
    monkeypatch.setattr(G, "_last_standdown", "")


class Clock:
    """A fake monotonic clock; sleeping advances it and fires what is due."""

    def __init__(self, t=1000.0):
        self.t, self.events = t, []

    def __call__(self):
        return self.t

    def at(self, when, fn):
        self.events.append((when, fn))

    async def sleep(self, dt):
        self.t += dt
        for event in [e for e in self.events if self.t >= e[0]]:
            self.events.remove(event)
            event[1]()


def ask_ends(clock):
    def end():
        idle._foreground_turns = 0
        idle._last_turn = clock()
    return end


# --- the gate on a fake clock --------------------------------------------------------------


def test_an_idle_ask_lets_the_call_go_at_once():
    clock = Clock()
    assert asyncio.run(ask_gate.wait_for_ask(clock=clock, sleep=clock.sleep)) == ask_gate.CLEAR
    assert clock() == 1000.0


def test_a_call_waits_while_ask_generates_and_resumes_after_the_grace():
    clock = Clock()
    idle._foreground_turns, idle._last_turn = 1, clock()
    clock.at(1005.0, ask_ends(clock))
    assert asyncio.run(ask_gate.wait_for_ask(clock=clock, sleep=clock.sleep)) == ask_gate.RESUMED
    assert 1005.0 + ask_gate.GRACE_S <= clock() <= 1005.0 + ask_gate.GRACE_S + ask_gate._POLL_S


def test_the_grace_after_a_turn_holds_a_call_too():
    clock = Clock()
    idle._last_turn = clock() - 0.5            # a turn ended half a second ago
    assert ask_gate.ask_busy(clock())
    assert asyncio.run(ask_gate.wait_for_ask(clock=clock, sleep=clock.sleep)) == ask_gate.RESUMED
    assert clock() >= 1000.0 - 0.5 + ask_gate.GRACE_S
    assert not ask_gate.ask_busy(clock())


def test_a_long_ask_session_cannot_starve_brief(capsys):
    clock = Clock()
    idle._foreground_turns = 1                 # never ends
    assert asyncio.run(ask_gate.wait_for_ask("triage", clock=clock, sleep=clock.sleep)) == ask_gate.FAIR
    assert ask_gate.MAX_WAIT_S <= clock() - 1000.0 <= ask_gate.MAX_WAIT_S + ask_gate._POLL_S
    out = capsys.readouterr().out
    assert "triage call waits for Ask" in out and "fairness cap" in out


# --- through _resident_reply, with a fake engine -------------------------------------------


@pytest.fixture
def model(monkeypatch):
    import service.config as config
    monkeypatch.setattr(config, "role_to_model", lambda role: "fast")
    monkeypatch.setattr(config, "no_thinking_capable", lambda: ["fast"])
    monkeypatch.setattr(config, "thinks_explicitly", lambda name: False)


@pytest.fixture
def fast_gate(monkeypatch):
    """Real time, shrunk: the gate polls every 5 ms with a 50 ms grace."""
    monkeypatch.setattr(ask_gate, "GRACE_S", 0.05)
    monkeypatch.setattr(ask_gate, "_POLL_S", 0.005)
    monkeypatch.setattr(ask_gate, "MAX_WAIT_S", 5.0)


class SerialEngine:
    """One request at a time, first come first served, like one resident model.
    A request the client walks away from keeps running (a non-streaming oMLX call
    isn't stopped at once), so it holds the engine either way."""
    managed = True

    def __init__(self, scale=1.0, brief=(0.3, 0.9)):
        self.scale, self.brief = scale, brief
        self.lock = asyncio.Lock()
        self.sent = self.cancelled = self.finished = 0
        self.ask_found_busy = []

    async def loaded_models(self):
        return ["fast"]

    def keep_warm(self):
        return set()

    async def run(self, prefill, decode, first=None):
        async with self.lock:
            await asyncio.sleep(prefill * self.scale)
            if first is not None:
                first.set_result(asyncio.get_running_loop().time())
            await asyncio.sleep(decode * self.scale)

    async def chat(self, model, messages, **kwargs):
        self.sent += 1
        try:
            await asyncio.shield(self.run(*self.brief))
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        self.finished += 1
        return {"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}]}


def use(monkeypatch, engine):
    from service.assistant import brief
    monkeypatch.setattr(brief, "_c", lambda: engine)
    return engine


def test_brief_waits_during_ask_and_resumes_on_its_own(monkeypatch, model, fast_gate):
    engine = use(monkeypatch, SerialEngine(scale=0.01))

    async def main():
        idle.begin_foreground()
        call = asyncio.create_task(G._resident_reply("s", "m", idle.foreground_busy))
        await asyncio.sleep(0.1)
        assert engine.sent == 0 and not call.done(), "nothing is sent while Ask generates"
        idle.end_foreground()
        await asyncio.sleep(0.02)
        assert engine.sent == 0, "nor in the grace after it"
        return await asyncio.wait_for(call, 1.0)
    assert asyncio.run(main()) == "ok" and engine.sent == 1


def test_a_running_brief_call_is_never_cancelled_when_ask_starts(monkeypatch, model, fast_gate):
    engine = use(monkeypatch, SerialEngine(scale=0.1))

    async def main():
        call = asyncio.create_task(G._resident_reply("s", "m", idle.foreground_busy))
        await asyncio.sleep(0.05)
        assert engine.sent == 1
        idle.begin_foreground()               # Ask starts mid-call
        try:
            return await asyncio.wait_for(call, 2.0)
        finally:
            idle.end_foreground()
    assert asyncio.run(main()) == "ok"
    assert engine.cancelled == 0 and engine.finished == 1


def test_the_fairness_slot_goes_beside_a_long_ask(monkeypatch, model, fast_gate, capsys):
    monkeypatch.setattr(ask_gate, "MAX_WAIT_S", 0.1)
    engine = use(monkeypatch, SerialEngine(scale=0.01))

    async def main():
        idle.begin_foreground()
        try:
            return await asyncio.wait_for(G._resident_reply("s", "m", idle.foreground_busy), 2.0)
        finally:
            idle.end_foreground()
    assert asyncio.run(main()) == "ok" and engine.finished == 1
    assert "fairness cap" in capsys.readouterr().out


def test_the_fairness_slot_never_passes_demo_modes_hold(monkeypatch, model, fast_gate):
    from service import stage_mode
    monkeypatch.setattr(ask_gate, "MAX_WAIT_S", 0.05)
    engine = use(monkeypatch, SerialEngine(scale=0.01))

    async def main():
        idle.begin_foreground()
        try:
            return await G._resident_reply("s", "m", G._scheduled_abort)
        finally:
            idle.end_foreground()
    stage_mode.set_active(True)
    try:
        assert asyncio.run(main()) is None and engine.sent == 0
    finally:
        stage_mode.set_active(False)


def test_remote_inference_never_waits(monkeypatch, model):
    import service.inference.remote_policy as rp
    monkeypatch.setattr(rp, "remote_only_profile", lambda: object())
    engine = use(monkeypatch, SerialEngine(scale=0.001))

    async def never(*args, **kwargs):
        raise AssertionError("a remote model shares nothing with Ask")
    monkeypatch.setattr(ask_gate, "wait_for_ask", never)
    assert asyncio.run(G._resident_reply("s", "m", lambda: False)) == "ok"


# --- the measurement: Ask's first token beside a Brief burst -------------------------------

_REAL_WAIT = ask_gate.wait_for_ask

# Modelled seconds (scaled down to run fast). A Brief call: 0.3 s reading a ~650-token
# prompt, 0.9 s writing ~60 tokens. An Ask step: 0.6 s to its first token, 3 s streaming.
SCALE = 0.04
ASK = (0.6, 3.0)


async def _burst(engine, calls, poll):
    """A Brief build that waits out a turn, as a patient cycle does, then goes on."""
    done = 0
    while done < calls:
        while idle.foreground_busy():
            await asyncio.sleep(poll)
        if await G._resident_reply("s", "m", idle.foreground_busy) is not None:
            done += 1
    return done


async def _ask_turn(engine):
    loop = asyncio.get_running_loop()
    idle.begin_foreground()
    try:
        first, started = loop.create_future(), loop.time()
        engine.ask_found_busy.append(engine.lock.locked())
        await engine.run(*ASK, first=first)
        return (first.result() - started) / SCALE
    finally:
        idle.end_foreground()


async def _session(engine, *, start, gaps, calls=8):
    """Ask turns from `start`, each next one `gap` after the last answer ended,
    with a Brief burst of `calls` running from t=0. Ask's first-token times, and
    when the burst finished (modelled seconds)."""
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    burst = asyncio.create_task(_burst(engine, calls, 0.25 * SCALE))
    ttfts = []
    await asyncio.sleep(start * SCALE)
    for gap in gaps:
        ttfts.append(await _ask_turn(engine))
        await asyncio.sleep(gap * SCALE)
    await burst
    return ttfts, (loop.time() - t0) / SCALE


def _measure(monkeypatch, gate_on, **session):
    if gate_on:
        monkeypatch.setattr(ask_gate, "wait_for_ask", _REAL_WAIT)
        monkeypatch.setattr(ask_gate, "GRACE_S", 2.0 * SCALE)
        monkeypatch.setattr(ask_gate, "_POLL_S", 0.25 * SCALE)
        monkeypatch.setattr(ask_gate, "MAX_WAIT_S", 60.0 * SCALE)
    else:
        async def clear(*args, **kwargs):
            return ask_gate.CLEAR
        monkeypatch.setattr(ask_gate, "wait_for_ask", clear)
    engine = use(monkeypatch, SerialEngine(scale=SCALE))
    monkeypatch.setattr(idle, "_foreground_turns", 0)
    monkeypatch.setattr(idle, "_last_turn", None)
    ttfts, finished = asyncio.run(_session(engine, **session))
    return ttfts, finished, engine


def test_measure_ask_first_token_beside_a_brief_burst(monkeypatch, model, capsys):
    rows = []
    # 1. One Ask arriving mid-call, at four points of a Brief call.
    for phase in (0.1, 0.4, 0.7, 1.0):
        for gate_on in (False, True):
            ttfts, finished, engine = _measure(monkeypatch, gate_on, start=2.4 + phase, gaps=[0.0])
            rows.append(("mid-call", phase, gate_on, ttfts, finished, engine.cancelled))
    # 2. Quick follow-ups: four Ask turns, each 1 s after the last answer (a confirm
    #    tap, a "and the other one?"), with the burst still owed.
    follow = {}
    for gate_on in (False, True):
        ttfts, finished, engine = _measure(monkeypatch, gate_on, start=1.5, gaps=[1.0, 1.0, 1.0, 0.0])
        follow[gate_on] = (ttfts, engine.ask_found_busy)
        rows.append(("follow-ups", None, gate_on, ttfts, finished, engine.cancelled))
    with capsys.disabled():
        print("\n[ask_gate measurement] Ask alone: first token %.1f s; one Brief call %.1f s" % (ASK[0], 1.2))
        for kind, phase, gate_on, ttfts, finished, cancelled in rows:
            label = "after " if gate_on else "before"
            at = f" at +{phase:.1f}s" if phase is not None else ""
            print(f"[ask_gate measurement] {kind}{at} {label}: first token "
                  + ", ".join(f"{t:.2f}" for t in ttfts)
                  + f" s; burst of 8 done at {finished:.1f} s; cancelled {cancelled}")
    # A call already running holds a serial engine either way, so Ask's first turn
    # waits for it in both. The follow-ups no longer find a Brief call in the gap.
    # (Asserted on who held the engine, not on timings, so load can't flake it.)
    before, after = follow[False][1], follow[True][1]
    assert before[0] and after[0]
    assert any(before[1:]) and not any(after[1:])


# --- a cycle mid-run waits at the gate instead of yielding ---------------------------------


def _gate_on(monkeypatch, clock):
    import functools
    monkeypatch.setattr(ask_gate, "wait_for_ask",
                        functools.partial(_REAL_WAIT, clock=clock, sleep=clock.sleep))


def test_a_cycle_mid_run_goes_on_as_soon_as_ask_is_idle(monkeypatch):
    from service.proactive import cycle
    clock = Clock()
    _gate_on(monkeypatch, clock)
    idle._foreground_turns, idle._last_turn = 1, clock()
    clock.at(1005.0, ask_ends(clock))
    assert asyncio.run(cycle._wait_out_turn(idle.foreground_busy, False)) is True
    assert 1005.0 + ask_gate.GRACE_S <= clock() <= 1005.0 + ask_gate.GRACE_S + ask_gate._POLL_S


def test_a_cycle_yields_past_the_fairness_cap(monkeypatch):
    from service.proactive import cycle
    clock = Clock()
    _gate_on(monkeypatch, clock)
    idle._foreground_turns = 1                 # never ends
    assert asyncio.run(cycle._wait_out_turn(idle.foreground_busy, False)) is False
    assert clock() - 1000.0 >= ask_gate.MAX_WAIT_S


def test_a_cycle_still_yields_at_once_for_an_abort_that_isnt_ask(monkeypatch):
    from service.proactive import cycle
    clock = Clock()
    _gate_on(monkeypatch, clock)
    assert asyncio.run(cycle._wait_out_turn(lambda: True, False)) is False
    assert clock() == 1000.0
