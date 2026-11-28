"""Long-overdue Harbor-only reminder records stop reading as current reminders, but a
direct read still lists them, apart and labelled."""
from __future__ import annotations

import asyncio
import os
import tempfile
import time
from unittest.mock import AsyncMock, patch

# Import the singleton only after redirecting its default database.
_SCRATCH = tempfile.TemporaryDirectory(prefix="harbor-stale-reminders-")
os.environ["HOME"] = _SCRATCH.name

from service.assistant import sync_status  # noqa: E402
from service.tools import assistant_tools  # noqa: E402

DAY = 86400
READY = {"sources": [{"id": "reminders", "state": "ready"}], "reminders_fresh": True}


def _row(title: str, age_days: float, source: str = "manual", dup=()) -> dict:
    return {"id": title, "source": source, "title": title,
            "when_ts": time.time() - age_days * DAY, "duplicate_sources": list(dup)}


def _search(rows: list[dict], readiness: dict = READY) -> str:
    with patch.object(sync_status, "ensure_sources", new_callable=AsyncMock,
                      return_value=readiness), \
         patch.object(assistant_tools, "reminders_matching", return_value=rows):
        return asyncio.run(assistant_tools.search_reminders("x"))


OLDER = "Older Harbor-only records, overdue by more than 14 days"


def _older_part(answer: str) -> tuple[str, str]:
    head, _, tail = answer.partition(OLDER)
    return head, tail


def test_old_harbor_only_record_is_listed_apart_from_current_ones() -> None:
    answer = _search([_row("synthetic recent", 2), _row("synthetic ancient", 40),
                      _row("synthetic ancient two", 90)])
    head, tail = _older_part(answer)
    assert "synthetic recent" in head
    assert "synthetic ancient" not in head, "never listed as a current reminder"
    assert "- synthetic ancient —" in tail and "- synthetic ancient two —" in tail
    assert "not listed" not in answer


def test_apple_backed_and_future_rows_are_never_set_apart() -> None:
    answer = _search([_row("synthetic apple", 400, source="reminders"),
                      _row("synthetic mirrored", 400, dup=("reminders",)),
                      _row("synthetic future", -3)])
    for title in ("synthetic apple", "synthetic mirrored", "synthetic future"):
        assert title in answer
    assert OLDER not in answer


def test_only_stale_rows_still_explain_the_empty_result_and_list_them() -> None:
    answer = _search([_row("synthetic ancient", 60)])
    head, tail = _older_part(answer)
    assert "synthetic ancient" not in head
    assert "no active match" in head
    assert "- synthetic ancient —" in tail


def test_unavailable_reminders_path_also_lists_them_apart() -> None:
    answer = _search([_row("synthetic recent", 1), _row("synthetic ancient", 50)],
                     {"sources": [{"id": "reminders", "state": "unavailable"}]})
    head, tail = _older_part(answer)
    assert "synthetic recent" in head and "synthetic ancient" not in head
    assert "- synthetic ancient —" in tail
