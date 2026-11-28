"""Completing a reminder that is already overdue grounds to it (audit 4.3).

Finishing something after it was due is the usual case; complete_reminder accepts it, so the
typed engine must not answer "I couldn't find" for a reminder more than five minutes late.
"""
from datetime import datetime, timedelta

from service.tasks import engine
from service.tasks.compiler import compile_task

NOW = datetime(2026, 9, 21, 9, 0)


class FakeStore:
    def __init__(self, rows):
        self.rows = rows

    def active_between(self, start, end, *, include_stale=False):
        return [r for r in self.rows
                if (start is None or r["when_ts"] >= start) and (end is None or r["when_ts"] < end)]


def _row(rid, title, when):
    return {"id": rid, "title": title, "when_ts": when.timestamp(), "source": "manual"}


def _plan(prompt):
    plan = compile_task(prompt, now=NOW)
    assert plan and plan.intent == "reminder.complete" and not plan.missing_slots
    return plan


def test_a_reminder_two_days_overdue_can_be_completed():
    plan = _plan("Complete the Laundry reminder.")
    store = FakeStore([_row("r1", "Laundry", NOW - timedelta(days=2))])
    message, event = engine._resolve_operation_targets(plan, store, now=NOW)
    assert (message, event) == ("", "")
    assert [t["id"] for t in plan.resolved_targets] == ["r1"]


def test_an_upcoming_reminder_still_completes_and_two_matches_still_ask():
    plan = _plan("Complete the Laundry reminder.")
    store = FakeStore([_row("r2", "Laundry", NOW + timedelta(hours=2))])
    assert engine._resolve_operation_targets(plan, store, now=NOW) == ("", "")
    assert [t["id"] for t in plan.resolved_targets] == ["r2"]
    plan = _plan("Complete the Laundry reminder.")
    store = FakeStore([_row("a", "Laundry", NOW - timedelta(days=3)),
                       _row("b", "Laundry", NOW + timedelta(days=1))])
    _, event = engine._resolve_operation_targets(plan, store, now=NOW)
    assert event == "target_ambiguous" and plan.resolved_targets == []
