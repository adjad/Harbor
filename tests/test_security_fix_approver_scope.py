"""#24: an approval whose verdict and scope contradict each other ({approved: true,
scope: "never"} or {approved: false, scope: "always"}) records no standing answer and
is a denial. Invented tool arguments; grants are replaced, nothing is written.
"""
from __future__ import annotations

import asyncio

import pytest

from service.agent import approver as A



@pytest.mark.parametrize("approved,scope", [(True, "never"), (False, "always")])
def test_a_contradictory_answer_records_nothing_and_is_a_denial(monkeypatch, approved, scope):
    recorded = []
    monkeypatch.setattr(A.grants, "grant", lambda *a, **k: recorded.append(k))

    async def run():
        events = []

        async def emit(ev):
            events.append(ev)

        ap = A.InteractiveApprover(emit, timeout=5)
        task = asyncio.create_task(ap.confirm({"id": "fixture-1", "tool": "write_file",
                                               "args": {"path": "~/Documents/fixture.txt"}}))
        await asyncio.sleep(0)
        assert ap.resolve("fixture-1", approved, scope)
        return await task

    assert asyncio.run(run()) is False
    assert recorded == []


@pytest.mark.parametrize("approved,scope,decision", [(True, "always", "allow"), (False, "never", "deny")])
def test_consistent_standing_answers_still_record(monkeypatch, approved, scope, decision):
    recorded = []
    monkeypatch.setattr(A.grants, "grant", lambda *a, **k: recorded.append(k["decision"]))
    A._apply_scope({"tool": "write_file", "args": {}}, approved, scope)
    assert recorded == [decision]
