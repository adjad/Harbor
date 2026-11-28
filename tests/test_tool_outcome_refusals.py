"""classify_tool_outcome: refusals, zero-match clears and unverified moves.

Invented receipts only; no tool runs.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_scratch = tempfile.TemporaryDirectory(prefix="harbor-outcome-")
os.environ.setdefault("HARBOR_HOME", _scratch.name)

import service.tools  # noqa: E402,F401  (registers every tool)
from service.tools.registry import classify_tool_outcome  # noqa: E402


@pytest.mark.parametrize("tool, text", [
    ("move_path", "(no such path: ~/Downloads/missing.pdf)"),
    ("move_path", "(refusing to overwrite ~/Documents/a.txt — it already exists.)"),
    ("write_document", "(refusing to overwrite ~/Documents/plan.docx — it already exists. Pick a different name.)"),
    ("spreadsheet_ops", "(refusing to overwrite ~/Documents/budget.xlsx — it already exists. Pick a different name.)"),
    ("write_file", "(macOS blocked writing ~/Desktop/x.txt — Harbor does not have permission for that folder.)"),
])
def test_parenthesized_refusals_from_mutating_tools_are_failures(tool, text):
    assert classify_tool_outcome(tool, text).status == "failed"


def test_a_read_tool_notice_in_parentheses_is_unchanged():
    assert classify_tool_outcome("read_file", "(no such file: ~/notes.txt)").status == "succeeded"


@pytest.mark.parametrize("tool, text", [
    ("clear_past_reminders", "Nothing past-due to clear."),
    ("clear_reminders", "No active reminders found for today."),
])
def test_zero_match_clears_are_no_match_not_deleted(tool, text):
    outcome = classify_tool_outcome(tool, text)
    assert outcome.status == "no_match"


def test_a_real_clear_is_still_a_delete():
    outcome = classify_tool_outcome("clear_past_reminders", "Cleared 3 past-due reminders.")
    assert outcome.status == "succeeded" and outcome.effect == "deleted"


def test_unconfirmed_event_move_is_unknown():
    text = "(not confirmed: Calendar may have moved “Dentist”; check it in Calendar.)"
    assert classify_tool_outcome("update_event", text).status == "unknown"
    assert classify_tool_outcome("update_event", "(couldn't move “Dentist”: refused.)").status == "failed"
