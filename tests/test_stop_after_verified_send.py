"""A Stop that lands just after a send went out leaves the turn saying it was SENT.

The send tools note each confirmed send before any later await (the Brief
bookkeeping, showing the result). When the app stops the turn after that point,
the saved note names what went out and says not to send it again, instead of
"could not be completed", which invites a duplicate send. An unconfirmed send
is never claimed. Invented people and messages only; nothing native, nothing live.
"""
import asyncio
import json
import socket
import subprocess
import types
from unittest.mock import AsyncMock

import pytest

from service import main
from service.proactive import handled
from service.tools import action_tools

HANDLE = "+15550104455"


@pytest.fixture
def turn(monkeypatch):
    main.SESSIONS.clear()
    monkeypatch.setattr(main, "client", AsyncMock(), raising=False)
    main.client.loaded_models.return_value = [main.role_target("fast").model]
    monkeypatch.setattr(main, "ensure_omlx", AsyncMock())
    monkeypatch.setattr(main, "maybe_summarize", AsyncMock())

    def forbidden(*a, **k):
        raise AssertionError("no sockets or native processes")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(action_tools, "_named", lambda handle, typed=None: "Quinn", raising=False)
    state = types.SimpleNamespace(delivery="sent", in_bookkeeping=asyncio.Event)

    async def stuck_bookkeeping(**_):
        state.reached.set()
        await asyncio.Event().wait()     # the Stop lands here, after the send went out
    monkeypatch.setattr(handled, "mark_sent_reply_async", stuck_bookkeeping)

    async def bridge(kind, payload, **_):
        return {"ok": True, "status": "succeeded", "delivery": state.delivery}
    monkeypatch.setattr(action_tools, "app_request", bridge)

    async def run_agent(client, model, messages, emit, approver, **kw):
        await emit({"type": "tool_call", "id": "call_0", "name": "send_message", "args": {}})
        with action_tools.human_reviewed_content():
            out = await action_tools.send_message(to=HANDLE, text="On my way, ten minutes.")
        state.reached.set()
        await emit({"type": "tool_result", "id": "call_0", "result": str(out)})
        await asyncio.Event().wait()     # or here, before the answer
    monkeypatch.setattr(main, "run_agent", run_agent, raising=False)
    yield state
    main.SESSIONS.clear()


def _stopped_note(state):
    async def scenario():
        state.reached = asyncio.Event()
        iterator = (await main.agent({"prompt": "delete the folder ~/Desktop/old-stuff"})).body_iterator
        first = json.loads((await asyncio.wait_for(iterator.__anext__(), 20))[6:])
        sid = first["id"]
        await asyncio.wait_for(state.reached.wait(), 20)
        await iterator.aclose()                       # Stop
        await asyncio.sleep(0.2)
        return main.store.turns_from(sid, 0)[-1]["content"]
    return asyncio.run(scenario())


def test_a_stop_after_a_confirmed_text_says_it_was_sent(turn):
    note = _stopped_note(turn)
    assert note.startswith("(Sent: the text to Quinn"), note
    assert "Don't send it again" in note
    assert "could not be completed" not in note and "outcome is unknown" not in note


def test_a_stop_after_an_unconfirmed_text_claims_nothing(turn):
    turn.delivery = "uncertain"
    note = _stopped_note(turn)
    assert "Sent:" not in note
    assert "could not be completed" in note


def test_no_turn_means_no_record():
    action_tools._note_verified_send("the text to nobody")   # outside a turn: kept nowhere
    sends = action_tools.begin_verified_sends()
    action_tools._note_verified_send("the text to Quinn")
    assert sends == ["the text to Quinn"]
