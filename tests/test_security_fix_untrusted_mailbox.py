"""#22: under Full access, once a turn has read mail, a message, a page or a file Harbor
did not write, archiving or marking mail and adding a reminder ask first, since the
text read may have chosen them. Before any such read they run as Full access allows.
Invented arguments; no mailbox or Reminders is touched.
"""
from __future__ import annotations

import inspect

import pytest

from service.safety import policy


@pytest.fixture(autouse=True)
def full_access(monkeypatch):
    monkeypatch.setattr(policy, "_FULL_ACCESS", True)


CALLS = [
    ("email_triage", "email_triage", {"action": "archive", "message_id": "<fixture-1@example.invalid>"}),
    ("email_triage", "email_triage", {"action": "mark_read", "message_id": "<fixture-2@example.invalid>"}),
    ("add_reminder", "assistant_write", {"title": "Fixture reminder", "when_iso": "2026-12-01T09:00"}),
    ("update_reminder", "assistant_write", {"title": "Fixture reminder", "when_iso": "2026-12-02T09:00"}),
    ("complete_reminder", "assistant_write", {"title": "Fixture reminder"}),
]


@pytest.mark.parametrize("tool,category,args", CALLS)
def test_full_access_allows_it_before_an_untrusted_read(tool, category, args):
    # Before any such read the loop applies decide() alone.
    assert policy.decide(category, args, tool=tool).tier is policy.Tier.ALLOW


@pytest.mark.parametrize("tool,category,args", CALLS)
def test_after_an_untrusted_read_it_asks(tool, category, args):
    decision = policy.decide(category, args, tool=tool)
    assert decision.tier is policy.Tier.ALLOW          # what Full access said before the fix
    after = policy.after_untrusted_read_decision(tool, category, args, decision)
    assert after.tier is policy.Tier.CONFIRM and "did not write" in after.reason


def test_other_writes_are_unchanged():
    decision = policy.decide("fs_write", {"path": "~/Documents/fixture.txt", "content": "x"},
                             tool="write_file")
    assert policy.after_untrusted_read_decision("write_file", "fs_write", {}, decision) is decision


def test_the_agent_loop_applies_it_once_a_turn_has_read_untrusted_text():
    from service.agent import loop
    source = inspect.getsource(loop)
    at = source.index("after_untrusted_read_decision(")
    assert "untrusted_read_in_turn[0]" in source[at - 400:at]
