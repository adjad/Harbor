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
OTHER = "+15550109911"


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
    state = types.SimpleNamespace(delivery="sent", second=False, calls=0)

    async def bookkeeping(**_):
        if state.second:
            return
        state.reached.set()
        await asyncio.Event().wait()     # the Stop lands here, after the send went out
    monkeypatch.setattr(handled, "mark_sent_reply_async", bookkeeping)

    async def bridge(kind, payload, **_):
        state.calls += 1
        if state.calls == 2:             # the second text is with Messages when Stop comes
            state.reached.set()
            await asyncio.Event().wait()
        return {"ok": True, "status": "succeeded", "delivery": state.delivery}
    monkeypatch.setattr(action_tools, "app_request", bridge)

    async def run_agent(client, model, messages, emit, approver, **kw):
        await emit({"type": "tool_call", "id": "call_0", "name": "send_message", "args": {}})
        with action_tools.human_reviewed_content():
            out = await action_tools.send_message(to=HANDLE, text="On my way, ten minutes.")
        if not state.second:
            state.reached.set()
        await emit({"type": "tool_result", "id": "call_0", "result": str(out)})
        if state.second:
            await emit({"type": "tool_call", "id": "call_1", "name": "send_message", "args": {}})
            with action_tools.human_reviewed_content():
                await action_tools.send_message(to=OTHER, text="Running late, start without me.")
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


def test_a_confirmed_send_and_a_second_one_still_going_out_says_both(turn):
    turn.second = True
    note = _stopped_note(turn)
    assert note.startswith("(Sent: the text to Quinn"), note
    assert "Another send was also attempted; its outcome is unknown" in note


# --- the record itself --------------------------------------------------------------------

def _send(delivery):
    async def bridge(kind, payload, **_):
        return {"ok": True, "status": "succeeded", **({"delivery": delivery} if delivery else {})}
    real = action_tools.app_request
    action_tools.app_request = bridge
    try:
        with action_tools.human_reviewed_content():
            return asyncio.run(action_tools.send_message(to=HANDLE, text="On my way."))
    finally:
        action_tools.app_request = real


@pytest.fixture
def record(monkeypatch):
    monkeypatch.setattr(action_tools, "_named", lambda handle, typed=None: "Quinn", raising=False)

    async def quiet(**_):
        return None
    monkeypatch.setattr(handled, "mark_sent_reply_async", quiet)
    yield action_tools.begin_turn_sends()
    action_tools._TURN_SENDS.set(None)


@pytest.mark.parametrize("delivery, state", [("sent", "sent"), ("delivered", "sent"), ("failed", "not_sent"),
                                             ("uncertain", "unknown"), (None, "unknown")])
def test_each_send_keeps_its_own_outcome(record, delivery, state):
    _send(delivery)
    assert [(e["what"], e["state"]) for e in record] == [("the text to Quinn", state)]
    assert action_tools.unconfirmed_sends(record) is (state == "unknown")


def test_a_claimed_send_is_judged_by_its_own_tool_outcome(record):
    plan = types.SimpleNamespace(status="running")
    action_tools.note_effect_claim(plan, "send_message")
    assert action_tools.unconfirmed_sends(record), "claimed and not yet sent: unknown"
    _send("sent")
    assert [(e["state"], e["plan"]) for e in record] == [("sent", plan)]
    assert not action_tools.unconfirmed_sends(record), "its send was confirmed: nothing unknown"


def test_a_claim_whose_plan_completed_without_a_tracked_send_is_not_unknown(record):
    action_tools.note_effect_claim(types.SimpleNamespace(status="completed"), "send_message")
    assert not action_tools.unconfirmed_sends(record)


def test_a_send_refused_before_it_left_is_not_recorded(record):
    with action_tools.human_reviewed_content():
        asyncio.run(action_tools.send_message(to=HANDLE, text=""))
    assert record == []


def test_no_turn_means_no_record(monkeypatch):
    monkeypatch.setattr(action_tools, "_TURN_SENDS", action_tools.ContextVar("isolated", default=None))
    monkeypatch.setattr(action_tools, "_named", lambda handle, typed=None: "Quinn", raising=False)

    async def quiet(**_):
        return None
    monkeypatch.setattr(handled, "mark_sent_reply_async", quiet)
    assert _send("sent").startswith("Message sent to ")   # outside a turn: kept nowhere, no error


# --- Auditor notes on fcbcc98e -------------------------------------------------------------

ENVELOPE = {"message_id": "<ox1@example.org>", "account": "Work", "account_id": "acct-1",
            "from": "me@example.com", "to": ["rowan@example.org"], "cc": [], "bcc": [],
            "subject": "Re: Agenda", "content": "Here it is, see you Thursday."}
NOT_ATTEMPTED = {"ok": False, "status": "not_attempted", "dispatch_started": False, "error": "Mail is closed"}
UNCONFIRMED = {"ok": False, "status": "unknown", "dispatch_started": True}
TOOLS = {
    "send_email": (lambda: action_tools.send_email(to="rowan@example.org", subject="Agenda",
                                                   body="Here it is, see you Thursday."),
                   {"ok": True, "status": "succeeded"}, "the email to rowan@example.org"),
    "reply_to_email": (lambda: action_tools.reply_to_email(
                           message_id=ENVELOPE["message_id"], body=ENVELOPE["content"], account="Work",
                           expected_reply=ENVELOPE),
                       {"ok": True, "accepted": True, "status": "succeeded", "reply": ENVELOPE},
                       "the email reply to rowan@example.org"),
    "forward_email": (lambda: action_tools.forward_email(message_id="<ox1@example.org>", to="rowan@example.org"),
                      {"ok": True, "status": "succeeded",
                       "forward": {"to": ["rowan@example.org"], "message_id": "<ox1@example.org>"}},
                      "the forward to Quinn"),
}


def _call(tool, answer):
    async def bridge(kind, payload, **_):
        if isinstance(answer, BaseException):
            raise answer
        return answer
    real = action_tools.app_request
    action_tools.app_request = bridge
    try:
        with action_tools.human_reviewed_content():
            return asyncio.run(tool())
    finally:
        action_tools.app_request = real


@pytest.fixture
def mail_record(record, monkeypatch):
    monkeypatch.setattr(action_tools, "_own_address_guard", lambda *a, **k: "")
    return record


@pytest.mark.parametrize("name", sorted(TOOLS))
@pytest.mark.parametrize("answer, state", [("ok", "sent"), (NOT_ATTEMPTED, "not_sent"), (UNCONFIRMED, "unknown")])
def test_each_mail_send_tool_keeps_its_own_outcome(mail_record, name, answer, state):
    tool, ok, what = TOOLS[name]
    _call(tool, ok if answer == "ok" else answer)
    assert [(e["what"], e["state"]) for e in mail_record] == [(what, state)]


def test_a_forward_mail_did_not_confirm_is_unknown(mail_record):
    tool, ok, _ = TOOLS["forward_email"]
    _call(tool, {"ok": True, "status": "succeeded"})          # no receipt naming this email
    assert [e["state"] for e in mail_record] == ["unknown"]


@pytest.mark.parametrize("name", ["send_message", *sorted(TOOLS)])
def test_an_exception_while_sending_leaves_the_send_pending(mail_record, name):
    tool = (lambda: action_tools.send_message(to=HANDLE, text="On my way.")) if name == "send_message" \
        else TOOLS[name][0]
    with pytest.raises(RuntimeError):
        _call(tool, RuntimeError("bridge went away"))
    assert [e["state"] for e in mail_record] == ["pending"]
    assert action_tools.unconfirmed_sends(mail_record)


def test_only_a_marked_refusal_after_dispatch_is_not_sent(record):
    """An unmarked failure after the send left proves nothing went out: unknown."""
    from service.tools.registry import _NotDeliveredResult

    def fake(result):
        @action_tools._tracked_send("send_message")
        async def tool():
            action_tools._dispatching("the text to Quinn")
            return result
        return tool
    for result in ("(error: Messages quit unexpectedly)", action_tools._not_sent("(NOT sent: no route)"),
                   _NotDeliveredResult("The text to Quinn was not delivered.")):
        asyncio.run(fake(result)())
    assert [e["state"] for e in record] == ["unknown", "not_sent", "not_sent"]


def test_an_unrelated_send_never_takes_over_another_plans_claim(mail_record):
    """Plan P claimed send_message, and its tool never reached the app. A later send of
    another kind, or a later text outside P, gets its own entry; P's claim stays
    unknown instead of being settled (and hidden) by someone else's confirmed send."""
    plan = types.SimpleNamespace(status="running")
    action_tools.note_effect_claim(plan, "send_message")
    tool, ok, _ = TOOLS["send_email"]
    _call(tool, ok)                                            # another tool: not P's
    assert [(e["state"], e["plan"]) for e in mail_record] == [("claimed", plan), ("sent", None)]
    with pytest.raises(RuntimeError):                          # P's own text, failing before it left
        asyncio.run(_raising_text())
    _send("sent")                                              # an unrelated text, later
    assert [(e["state"], e["plan"]) for e in mail_record] == [("claimed", plan), ("sent", None), ("sent", None)]
    assert action_tools.unconfirmed_sends(mail_record), "P's claim must still read as unknown"


async def _raising_text():
    @action_tools._tracked_send("send_message")
    async def tool():
        raise RuntimeError("contacts unavailable")             # before any dispatch
    await tool()


def test_a_claim_refused_by_its_own_tool_before_dispatch_is_not_sent(record):
    plan = types.SimpleNamespace(status="running")
    action_tools.note_effect_claim(plan, "send_message")
    with action_tools.human_reviewed_content():
        asyncio.run(action_tools.send_message(to=HANDLE, text=""))   # refused: empty text
    _send("sent")                                                    # an unrelated text
    assert [(e["state"], e["plan"]) for e in record] == [("not_sent", plan), ("sent", None)]
    assert not action_tools.unconfirmed_sends(record)


def test_the_task_claim_hook_names_the_claimed_steps_tool():
    plan = types.SimpleNamespace(id="p1", revision=3, steps=[types.SimpleNamespace(id="s1", tool="resolve_contact"),
                                                            types.SimpleNamespace(id="s2", tool="send_message")])
    assert main._task_step_tool(plan, "task_p1_s2_3") == "send_message"
    assert main._task_step_tool(plan, "task_p1_s2_2") == ""


def test_the_workflow_executor_claim_hook_binds_its_send(tmp_path, monkeypatch):
    """A real workflow send: its durable claim and the send tool's outcome are one entry."""
    from service.memory.store import SessionStore
    from service.safety.policy import Decision, Tier
    from service.workflows import executor as E
    from service.workflows.models import WorkflowPlan
    monkeypatch.setattr(E, "decide", lambda *a, **kw: Decision(Tier.ALLOW, "synthetic"))
    monkeypatch.setattr(action_tools, "_own_address_guard", lambda *a, **k: "")
    monkeypatch.setattr(action_tools, "_named", lambda handle, typed=None: "Quinn", raising=False)

    async def quiet(**_):
        return None
    monkeypatch.setattr(handled, "mark_sent_reply_async", quiet)

    async def bridge(kind, payload, **_):
        return {"ok": True, "status": "succeeded", "delivery": "sent"}
    monkeypatch.setattr(action_tools, "app_request", bridge)
    store = SessionStore(tmp_path / "sessions.db")
    sid = store.create_session()
    plan = WorkflowPlan(recipient=HANDLE, channel="messages", delivery="send", artifact_text="On my way.",
                        artifact_provenance="verified_tool_receipt", status="running")
    store.save_workflow(sid, plan.to_dict())

    async def confirm(action):
        return True

    async def run():
        sends = action_tools.begin_turn_sends()
        async def emit(event):
            return None
        await E.execute_workflow(plan, emit, types.SimpleNamespace(confirm=confirm), store=store, session_id=sid)
        return sends
    try:
        sends = asyncio.run(run())
    finally:
        store._db.close()
    assert [(e["state"], e["plan"], e["operation"]) for e in sends] == [("sent", plan, "send_message")]


def _settled_task_note(monkeypatch, *, claim_now):
    """A typed send task stopped mid-turn. `claim_now`: this turn claims and confirms
    its send; otherwise the claim came from an earlier turn and this one never ran."""
    plan = types.SimpleNamespace(status="running", intent="message.send", claimed_calls=["task_c"],
                                 to_dict=lambda: {"status": plan.status})
    task = types.SimpleNamespace(executable=True, response="", plan=plan, event="planned", trace=[])
    reached = {}

    async def prepare(*a, **k):
        return task

    async def execute(plan_, emit, approver, **k):
        if claim_now:
            action_tools.note_effect_claim(plan_, "send_message")
            with action_tools.human_reviewed_content():
                await action_tools.send_message(to=HANDLE, text="On my way, ten minutes.")
        reached["event"].set()
        await asyncio.Event().wait()
    monkeypatch.setattr("service.tasks.reply_engine.prepare_task_turn_async", prepare)
    monkeypatch.setattr(main, "execute_task", execute)
    monkeypatch.setattr(main, "finish_task", lambda *a, **k: None)   # the plan stays "running"

    async def scenario():
        reached["event"] = asyncio.Event()
        iterator = (await main.agent({"prompt": "text Quinn I'm on my way"})).body_iterator
        sid = json.loads((await asyncio.wait_for(iterator.__anext__(), 20))[6:])["id"]
        await asyncio.wait_for(reached["event"].wait(), 20)
        await iterator.aclose()
        await asyncio.sleep(0.2)
        return main.store.turns_from(sid, 0)[-1]["content"]
    return asyncio.run(scenario())


def test_an_untracked_task_claim_keeps_the_plan_level_warning(turn, monkeypatch):
    note = _settled_task_note(monkeypatch, claim_now=False)
    assert "Sending was already attempted; its outcome is unknown" in note
    assert "Sent:" not in note


def test_a_tracked_task_send_that_was_confirmed_gets_no_warning(turn, monkeypatch):
    turn.second = True                                   # the fixture's bookkeeping returns at once
    note = _settled_task_note(monkeypatch, claim_now=True)
    assert note.startswith("(Sent: the text to Quinn"), note
    assert "outcome is unknown" not in note


# --- Auditor notes on 415fb953 -------------------------------------------------------------

def _texting(answer=None):
    async def bridge(kind, payload, **_):
        if isinstance(answer, BaseException):
            raise answer
        return answer or {"ok": True, "status": "succeeded", "delivery": "sent"}
    return bridge


async def _text(to=HANDLE):
    with action_tools.human_reviewed_content():
        return await action_tools.send_message(to=to, text="On my way.")


def test_a_claim_whose_tool_never_ran_is_closed_when_its_run_ends(record, monkeypatch):
    """run_tool refused before the tool body (remote-only, a name mismatch): the claim
    stays unknown, and the next text, someone else's, never takes it over."""
    monkeypatch.setattr(action_tools, "app_request", _texting())
    plan = types.SimpleNamespace(status="running")

    async def run():
        action_tools.note_effect_claim(plan, "send_message")
        with action_tools.effect_claim_scope():
            pass                                      # the tool never ran
        await _text(OTHER)
    asyncio.run(run())
    assert [(e["state"], e["plan"]) for e in record] == [("claimed", plan), ("sent", None)]
    assert action_tools.unconfirmed_sends(record)


def test_two_parallel_calls_of_the_claimed_tool_take_the_claim_once(record, monkeypatch):
    monkeypatch.setattr(action_tools, "app_request", _texting())
    plan = types.SimpleNamespace(status="running")

    async def run():
        action_tools.note_effect_claim(plan, "send_message")
        await asyncio.gather(_text(), _text(OTHER))
    asyncio.run(run())
    assert sorted((e["state"], e["plan"] is plan) for e in record) == [("sent", False), ("sent", True)]
    assert len(record) == 2


def test_a_claimed_tool_run_in_a_copied_context_keeps_the_claim_to_itself(record, monkeypatch):
    monkeypatch.setattr(action_tools, "app_request", _texting())
    plan = types.SimpleNamespace(status="running")

    async def run():
        action_tools.note_effect_claim(plan, "send_message")
        await asyncio.create_task(_text())            # the child sees a copy of the context
        await _text(OTHER)                            # the parent still holds the claim pointer
    asyncio.run(run())
    assert [(e["state"], e["plan"]) for e in record] == [("sent", plan), ("sent", None)]


def test_a_claim_that_raises_after_dispatch_stays_unknown(record, monkeypatch):
    monkeypatch.setattr(action_tools, "app_request", _texting(RuntimeError("Messages went away")))
    plan = types.SimpleNamespace(status="running")

    async def run():
        action_tools.note_effect_claim(plan, "send_message")
        with action_tools.effect_claim_scope():
            await _text()
    with pytest.raises(RuntimeError):
        asyncio.run(run())
    assert [(e["what"], e["state"], e["plan"]) for e in record] == [("the text to Quinn", "pending", plan)]
    assert action_tools.unconfirmed_sends(record)
