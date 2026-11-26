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
  long chat can't starve a cold start. Each call gets its own wait, so during a
  long Ask session Brief gets at most one call per MAX_WAIT_S.
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
FAIR = "fair"          # waited MAX_WAIT_S: goes beside Ask


def ask_busy(now: float | None = None) -> bool:
    """A user-facing turn is in flight, or ended less than GRACE_S ago."""
    if idle.foreground_busy():
        return True
    last = idle.last_turn()
    now = time.monotonic() if now is None else now
    return last is not None and now - last < GRACE_S


def _log(text: str) -> None:
    print(f"[ask_gate] {text}", flush=True)


async def wait_for_ask(what: str = "brief", *, clock=time.monotonic, sleep=asyncio.sleep) -> str:
    """Wait until Ask is idle, at most MAX_WAIT_S. CLEAR, RESUMED or FAIR."""
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
        if now - start >= MAX_WAIT_S:
            _log(f"{what} call waited {now - start:.0f} s, the fairness cap: it goes beside Ask")
            return FAIR
