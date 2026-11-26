"""Swarm #6: deleting a chat while its turn is running used to put the turn's text
back as orphan rows (no session row, but in turns and the full-text index). The
delete now stops that chat's turn first, and the store writes turns only for a chat
that exists. Synthetic stores only: no engine, model, socket or native process.
"""
from __future__ import annotations

import asyncio

from service import main
from service.memory.store import SessionStore
# The disconnect fixtures: a fake engine, no sockets, a runner waiting on an approval.
from tests.test_agent_disconnect import isolated, read_until, start, turns  # noqa: F401


def test_deleting_a_chat_mid_turn_leaves_no_rows_behind(isolated):  # noqa: F811
    async def scenario():
        iterator = await start(prompt="tidy the folder ~/Desktop/example-old")
        seen = await read_until(iterator, "confirm")
        sid = next(e["id"] for e in seen if e["type"] == "session")
        assert any(entry["sid"] == sid for entry in main.SESSIONS.values())
        assert (await main.delete_session(sid)) == {"ok": True}
        assert isolated.cancelled, "the chat's running turn is stopped by the delete"
        await iterator.aclose()
        await asyncio.sleep(0.2)
        assert main.store.get_session(sid) is None
        assert turns(sid) == [], "nothing of the deleted chat is written back"
    asyncio.run(scenario())


def test_the_store_writes_no_turn_for_a_chat_that_is_gone(tmp_path):
    store = SessionStore(tmp_path / "sessions.db")
    sid = store.create_session()
    assert store.add_turn(sid, "user", "an example question") == 0
    store.delete_session(sid)
    assert store.add_turn(sid, "assistant", "a late answer") == -1
    store.set_summary(sid, "an example summary", 1)
    assert store.turns_from(sid, 0) == [] and store.get_session(sid) is None
    assert store.add_turn("never-created", "user", "text") == -1
