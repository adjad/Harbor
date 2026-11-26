"""Brief's model calls wait for Ask.

Quayside has one model on this Mac. oMLX 0.8.3 would batch a Brief call beside an
Ask turn, but Gemma loses MTP in a batch and the engine has no priority
(docs/omlx-0.8.3-concurrency.md), so Ask streams slower while they share. So a
background Brief call (triage, the judge, drafts, the gists, the cold-start
build: all go through mail_gists._resident_reply) starts only while Ask is idle.
Nothing here runs a model.

IMPORTANT rules:
- Ask is busy while a user-facing turn is in flight (idle.begin_foreground and
  end_foreground, which Ask's turn already calls) and for GRACE_S after the last
  one ended, so a call doesn't land in the gap before a follow-up's first step.
- A call waits here before it starts. One already running is never cancelled:
  it finishes, and Ask goes first at the next call boundary.
- Brief resumes on its own: the waiting call goes as soon as Ask is idle.
- Fairness: a call that has waited MAX_WAIT_S goes anyway, with a log line, so a
  long chat can't starve a cold start. The slot is global: at most one Brief call
  goes beside Ask per MAX_WAIT_S, however many are waiting. A caller that only
  waits (the cycle between items, fair=False) never takes it: past the cap it is
  told CAPPED and yields. The slot counts only when a call runs: a caller that
  took it and then stood down gives it back (release_fair).
- Waiting changes no status. The caller's "updating" stays as it is; a wait is
  never a failure.
"""
from __future__ import annotations

import asyncio
import time

from service import idle

GRACE_S = 2.0
MAX_WAIT_S = 60.0
_POLL_S = 0.25

CLEAR = "clear"        # Ask was idle: no wait
RESUMED = "resumed"    # waited, and Ask went idle
FAIR = "fair"          # waited MAX_WAIT_S and took the fairness slot: goes beside Ask
CAPPED = "capped"      # waited MAX_WAIT_S, fair=False: did not take the slot

# When the fairness slot was last taken (monotonic), for the one-per-window rule,
# and what it was before that take, for release_fair.
_last_fair: float | None = None
_fair_before: float | None = None
# Indirections so tests can drive the gate on a virtual clock.
_clock = time.monotonic
_sleep = asyncio.sleep


def ask_busy(now: float | None = None) -> bool:
    """A user-facing turn is in flight, or ended less than GRACE_S ago."""
    if idle.foreground_busy():
        return True
    last = idle.last_turn()
    now = _clock() if now is None else now
    return last is not None and now - last < GRACE_S


def _log(text: str) -> None:
    print(f"[ask_gate] {text}", flush=True)


async def wait_for_ask(what: str = "brief", *, fair: bool = True, clock=None, sleep=None) -> str:
    """Wait until Ask is idle. CLEAR, RESUMED, FAIR (took the global fairness slot)
    or, with fair=False, CAPPED after MAX_WAIT_S."""
    global _last_fair, _fair_before
    clock, sleep = clock or _clock, sleep or _sleep
    start = clock()
    if not ask_busy(start):
        return CLEAR
    _log(f"{what} call waits for Ask")
    while True:
        await sleep(_POLL_S)
        now = clock()
        if not ask_busy(now):
            _log(f"{what} call resumes after {now - start:.1f} s")
            return RESUMED
        if now - start < MAX_WAIT_S:
            continue
        if not fair:
            _log(f"{what} waited {now - start:.0f} s, the fairness cap: it yields")
            return CAPPED
        if _last_fair is None or now - _last_fair >= MAX_WAIT_S:
            _fair_before, _last_fair = _last_fair, now
            _log(f"{what} call waited {now - start:.0f} s, the fairness cap: it goes beside Ask")
            return FAIR


def fair_taken() -> float | None:
    """When the slot was last taken: read right after wait_for_ask returned FAIR."""
    return _last_fair


def release_fair(taken: float | None) -> None:
    """The caller took the slot at `taken` but no call ran: give the slot back. A no-op
    once someone else has taken it since."""
    global _last_fair
    if taken is not None and _last_fair == taken:
        _last_fair = _fair_before
        _log("no call ran: the fairness slot is free again")
