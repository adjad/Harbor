"""Brief's model calls wait for Ask (service/proactive/ask_gate.py).

Fake clocks and a fake engine only: no model is loaded and nothing is sent. Every
timed test runs on a virtual clock (an event loop whose time jumps to its next
timer), so a loaded host can't change a result.
"""
import asyncio
import os
import selectors
import tempfile
import types

_scratch = tempfile.TemporaryDirectory(prefix="harbor-ask-gate-")
os.environ.setdefault("HARBOR_HOME", _scratch.name)

import pytest

from service import idle
from service.assistant import mail_gists as G
from service.proactive import ask_gate

_REAL_WAIT = ask_gate.wait_for_ask


class _JumpSelector:
    """Never blocks: a wait for the next timer moves the loop's clock to it instead."""

    def __init__(self, loop, inner):
        self.loop, self.inner = loop, inner

    def select(self, timeout=None):
        if timeout:
            self.loop.now += timeout
        return self.inner.select(0)

    def __getattr__(self, name):
        return getattr(self.inner, name)


class VirtualLoop(asyncio.SelectorEventLoop):
    def __init__(self):
        super().__init__(selectors.DefaultSelector())
        self.now = 1000.0
        self._selector = _JumpSelector(self, self._selector)

    def time(self):
        return self.now


def _virtual(monkeypatch, loop):
    """idle and the gate read the loop's virtual clock."""
    monkeypatch.setattr(idle, "time", types.SimpleNamespace(monotonic=loop.time, time=lambda: 0.0))
    monkeypatch.setattr(ask_gate, "_clock", loop.time)
    monkeypatch.setattr(ask_gate, "_sleep", asyncio.sleep)


@pytest.fixture
def vrun(monkeypatch):
    """Run a coroutine on a virtual clock."""
    loop = VirtualLoop()
    _virtual(monkeypatch, loop)
    monkeypatch.setattr(ask_gate, "wait_for_ask", _REAL_WAIT)

    def run(coro):
        return loop.run_until_complete(coro)
    run.loop = loop
    yield run
    loop.close()


@pytest.fixture(autouse=True)
def quiet_ask(monkeypatch):
    """Every test starts with Ask idle and ends without leaving timing state behind."""
    from service import stage_mode
    monkeypatch.setattr(idle, "_foreground_turns", 0)
    monkeypatch.setattr(idle, "_last_turn", None)
    monkeypatch.setattr(ask_gate, "_last_fair", None)
    monkeypatch.setattr(ask_gate, "_fair_before", None)
    monkeypatch.setattr(G, "_last_standdown", "")
    yield
    stage_mode.set_active(False)
    idle._foreground_turns, idle._last_turn = 0, None
    ask_gate._last_fair = ask_gate._fair_before = None


async def _after(delay, fn):
    await asyncio.sleep(delay)
    fn()


# --- the gate on a virtual clock -----------------------------------------------------------


def test_an_idle_ask_lets_the_call_go_at_once(vrun):
    assert vrun(ask_gate.wait_for_ask()) == ask_gate.CLEAR
    assert vrun.loop.now == 1000.0


def test_a_call_waits_while_ask_generates_and_resumes_after_the_grace(vrun):
    async def main():
        idle.begin_foreground()
        asyncio.ensure_future(_after(5.0, idle.end_foreground))
        return await ask_gate.wait_for_ask()
    assert vrun(main()) == ask_gate.RESUMED
    assert 1005.0 + ask_gate.GRACE_S <= vrun.loop.now <= 1005.0 + ask_gate.GRACE_S + ask_gate._POLL_S


def test_the_grace_after_a_turn_holds_a_call_too(vrun):
    idle._last_turn = 1000.0 - 0.5            # a turn ended half a second ago
    assert ask_gate.ask_busy()
    assert vrun(ask_gate.wait_for_ask()) == ask_gate.RESUMED
    assert vrun.loop.now >= 1000.0 - 0.5 + ask_gate.GRACE_S and not ask_gate.ask_busy()


def test_a_long_ask_session_cannot_starve_brief(vrun, capsys):
    idle._foreground_turns = 1                 # never ends
    assert vrun(ask_gate.wait_for_ask("triage")) == ask_gate.FAIR
    assert ask_gate.MAX_WAIT_S <= vrun.loop.now - 1000.0 <= ask_gate.MAX_WAIT_S + ask_gate._POLL_S
    out = capsys.readouterr().out
    assert "triage call waits for Ask" in out and "fairness cap: it goes beside Ask" in out


def test_the_fairness_slot_is_global_one_call_per_window(vrun):
    """Three callers waiting through one long Ask: one goes per MAX_WAIT_S, not three."""
    idle._foreground_turns = 1
    went = []

    async def caller(name):
        assert await ask_gate.wait_for_ask(name) == ask_gate.FAIR
        went.append((name, vrun.loop.now - 1000.0))

    async def main():
        await asyncio.gather(caller("triage"), caller("gists"), caller("draft"))
    vrun(main())
    gaps = [b[1] - a[1] for a, b in zip(went, went[1:])]
    assert len(went) == 3 and went[0][1] >= ask_gate.MAX_WAIT_S
    assert all(gap >= ask_gate.MAX_WAIT_S for gap in gaps), went


def test_a_wait_only_caller_never_takes_the_slot(vrun, capsys):
    idle._foreground_turns = 1
    assert vrun(ask_gate.wait_for_ask("cycle", fair=False)) == ask_gate.CAPPED
    assert ask_gate._last_fair is None and "it yields" in capsys.readouterr().out


# --- a cycle mid-run waits at the gate instead of yielding ---------------------------------


def test_a_cycle_mid_run_goes_on_as_soon_as_ask_is_idle(vrun):
    from service.proactive import cycle

    async def main():
        idle.begin_foreground()
        asyncio.ensure_future(_after(5.0, idle.end_foreground))
        return await cycle._wait_out_turn(idle.foreground_busy, False)
    assert vrun(main()) is True
    assert 1005.0 + ask_gate.GRACE_S <= vrun.loop.now <= 1005.0 + ask_gate.GRACE_S + ask_gate._POLL_S


def test_a_cycle_yields_past_the_cap_without_taking_the_slot(vrun):
    from service.proactive import cycle
    idle._foreground_turns = 1                 # never ends
    assert vrun(cycle._wait_out_turn(idle.foreground_busy, False)) is False
    assert vrun.loop.now - 1000.0 >= ask_gate.MAX_WAIT_S and ask_gate._last_fair is None


def test_a_cycle_still_yields_at_once_for_an_abort_that_isnt_ask(vrun):
    from service.proactive import cycle
    assert vrun(cycle._wait_out_turn(lambda: True, False)) is False
    assert vrun.loop.now == 1000.0


# --- through _resident_reply, with a fake engine -------------------------------------------


@pytest.fixture
def model(monkeypatch):
    import service.config as config
    monkeypatch.setattr(config, "role_to_model", lambda role: "fast")
    monkeypatch.setattr(config, "no_thinking_capable", lambda: ["fast"])
    monkeypatch.setattr(config, "thinks_explicitly", lambda name: False)


class SerialEngine:
    """One request at a time, first come first served, like one resident model.
    A request the client walks away from keeps running (a non-streaming oMLX call
    isn't stopped at once), so it holds the engine either way."""
    managed = True

    def __init__(self, brief=(0.3, 0.9)):
        self.brief = brief
        self.lock = asyncio.Lock()
        self.sent, self.cancelled, self.finished = [], 0, 0
        self.ask_found_busy = []

    async def loaded_models(self):
        return ["fast"]

    def keep_warm(self):
        return set()

    async def run(self, prefill, decode, first=None):
        async with self.lock:
            await asyncio.sleep(prefill)
            if first is not None:
                first.set_result(asyncio.get_running_loop().time())
            await asyncio.sleep(decode)

    async def chat(self, model, messages, **kwargs):
        self.sent.append(asyncio.get_running_loop().time())
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


def test_brief_waits_during_ask_and_resumes_on_its_own(monkeypatch, model, vrun):
    engine = use(monkeypatch, SerialEngine())

    async def main():
        idle.begin_foreground()
        asyncio.ensure_future(_after(5.0, idle.end_foreground))
        return await G._resident_reply("s", "m", idle.foreground_busy)
    assert vrun(main()) == "ok"
    assert len(engine.sent) == 1 and engine.sent[0] >= 1005.0 + ask_gate.GRACE_S, \
        "nothing is sent while Ask generates, nor in the grace after it"


def test_a_running_brief_call_is_never_cancelled_when_ask_starts(monkeypatch, model, vrun):
    engine = use(monkeypatch, SerialEngine(brief=(0.3, 5.0)))

    async def main():
        call = asyncio.ensure_future(G._resident_reply("s", "m", idle.foreground_busy))
        await asyncio.sleep(1.0)
        assert len(engine.sent) == 1
        idle.begin_foreground()               # Ask starts mid-call
        try:
            return await call
        finally:
            idle.end_foreground()
    assert vrun(main()) == "ok"
    assert engine.cancelled == 0 and engine.finished == 1


def test_demo_mode_still_stops_a_running_scheduled_gist_call(monkeypatch, model, vrun):
    """Bounded: Demo mode's hold isn't stretched by a gist call of up to _TIMEOUT_S."""
    from service import stage_mode
    engine = use(monkeypatch, SerialEngine(brief=(0.3, 60.0)))

    async def main():
        call = asyncio.ensure_future(G._resident_reply("s", "m", G._scheduled_abort))
        await asyncio.sleep(1.0)
        stage_mode.set_active(True)
        started = vrun.loop.now
        reply = await call
        took = vrun.loop.now - started
        async with engine.lock:               # let the walked-away request drain
            pass
        return reply, took
    try:
        reply, took = vrun(main())
    finally:
        stage_mode.set_active(False)
    assert reply is None and engine.cancelled == 1 and took <= 0.5 + 0.01


def test_the_fairness_slot_goes_beside_a_long_ask(monkeypatch, model, vrun, capsys):
    engine = use(monkeypatch, SerialEngine())

    async def main():
        idle.begin_foreground()
        try:
            return await G._resident_reply("s", "m", idle.foreground_busy)
        finally:
            idle.end_foreground()
    assert vrun(main()) == "ok" and engine.finished == 1
    assert engine.sent[0] - 1000.0 >= ask_gate.MAX_WAIT_S
    assert "fairness cap" in capsys.readouterr().out


def test_the_fairness_slot_never_passes_demo_modes_hold(monkeypatch, model, vrun):
    from service import stage_mode
    engine = use(monkeypatch, SerialEngine())

    async def main():
        idle.begin_foreground()
        try:
            return await G._resident_reply("s", "m", G._scheduled_abort)
        finally:
            idle.end_foreground()
    stage_mode.set_active(True)
    try:
        assert vrun(main()) is None and engine.sent == []
    finally:
        stage_mode.set_active(False)
    assert ask_gate._last_fair is None, "no call ran, so the slot is free again"


def test_a_slot_given_back_goes_to_the_next_call(monkeypatch, model, vrun):
    engine = use(monkeypatch, SerialEngine())
    loaded = [[], ["fast"]]                    # the first caller finds the model gone

    async def loaded_models():
        return loaded.pop(0)
    engine.loaded_models = loaded_models

    async def main():
        idle.begin_foreground()               # a long Ask: both callers wait out the cap
        try:
            return await asyncio.gather(G._resident_reply("s", "m", idle.foreground_busy),
                                        G._resident_reply("s", "m", idle.foreground_busy))
        finally:
            idle.end_foreground()
    assert sorted(vrun(main()), key=str) == [None, "ok"]
    assert engine.sent[0] - 1000.0 < ask_gate.MAX_WAIT_S + 1, \
        "the caller that stood down gave the slot back: the other went in the same window"


def test_a_turn_ending_mid_check_never_cancels_a_scheduled_call(monkeypatch, model, vrun):
    engine = use(monkeypatch, SerialEngine(brief=(0.3, 5.0)))

    def abort():                               # the turn ends right after abort() reads it
        busy = idle.foreground_busy()
        if busy:
            idle.end_foreground()
        return busy

    async def main():
        call = asyncio.ensure_future(G._resident_reply("s", "m", abort))
        await asyncio.sleep(1.0)
        idle.begin_foreground()               # Ask starts mid-call
        return await call
    assert vrun(main()) == "ok"
    assert engine.cancelled == 0 and engine.finished == 1


def test_remote_inference_never_waits(monkeypatch, model):
    import service.inference.remote_policy as rp
    monkeypatch.setattr(rp, "remote_only_profile", lambda: object())
    engine = use(monkeypatch, SerialEngine(brief=(0.0, 0.0)))

    async def never(*args, **kwargs):
        raise AssertionError("a remote model shares nothing with Ask")
    monkeypatch.setattr(ask_gate, "wait_for_ask", never)
    assert asyncio.run(G._resident_reply("s", "m", lambda: False)) == "ok"


# --- the measurement: Ask's first token beside a Brief burst -------------------------------

# Modelled seconds on the virtual clock. A Brief call: 0.3 s reading a ~650-token
# prompt, 0.9 s writing ~60 tokens. An Ask step: 0.6 s to its first token, 3 s streaming.
ASK = (0.6, 3.0)


async def _burst(calls, poll=0.25):
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
        return first.result() - started
    finally:
        idle.end_foreground()


async def _session(engine, *, start, gaps, calls=8):
    """Ask turns from `start`, each next one `gap` after the last answer ended,
    with a Brief burst of `calls` running from t=0. Ask's first-token times, and
    when the burst finished."""
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    burst = asyncio.ensure_future(_burst(calls))
    ttfts = []
    await asyncio.sleep(start)
    for gap in gaps:
        ttfts.append(await _ask_turn(engine))
        await asyncio.sleep(gap)
    await burst
    return ttfts, loop.time() - t0


def _measure(monkeypatch, gate_on, **session):
    loop = VirtualLoop()
    _virtual(monkeypatch, loop)
    monkeypatch.setattr(idle, "_foreground_turns", 0)
    monkeypatch.setattr(idle, "_last_turn", None)
    monkeypatch.setattr(ask_gate, "_last_fair", None)
    if gate_on:
        monkeypatch.setattr(ask_gate, "wait_for_ask", _REAL_WAIT)
    else:
        async def clear(*args, **kwargs):
            return ask_gate.CLEAR
        monkeypatch.setattr(ask_gate, "wait_for_ask", clear)
    engine = use(monkeypatch, SerialEngine())
    try:
        ttfts, finished = loop.run_until_complete(_session(engine, **session))
    finally:
        loop.close()
    return ttfts, finished, engine


def test_measure_ask_first_token_beside_a_brief_burst(monkeypatch, model, capsys):
    rows, mid, follow = [], {}, {}
    # 1. One Ask arriving mid-call, at four points of a Brief call.
    for phase in (0.1, 0.4, 0.7, 1.0):
        for gate_on in (False, True):
            ttfts, finished, engine = _measure(monkeypatch, gate_on, start=2.4 + phase, gaps=[0.0])
            mid[phase, gate_on] = ttfts[0]
            rows.append(("mid-call", phase, gate_on, ttfts, finished, engine.cancelled))
    # 2. Quick follow-ups: four Ask turns, each 1 s after the last answer (a confirm
    #    tap, a "and the other one?"), with the burst still owed.
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
    # A call already running holds a serial engine either way: no worse after.
    for phase in (0.1, 0.4, 0.7, 1.0):
        assert mid[phase, True] <= mid[phase, False] + 1e-6
    # The follow-ups no longer find a Brief call in the gap: Ask alone's first token.
    (before, busy_before), (after, busy_after) = follow[False], follow[True]
    assert any(busy_before[1:]) and not any(busy_after[1:])
    assert after[1:] == pytest.approx([ASK[0]] * 3, abs=1e-6)
    assert min(before[1:]) > ASK[0] + 0.2
