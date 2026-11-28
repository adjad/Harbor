"""Router source subsets: every personal reader is reachable from its domain.

Synthetic prompts only; no model, no app, no user data. HARBOR_HOME is isolated.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_scratch = tempfile.TemporaryDirectory(prefix="harbor-router-subsets-")
os.environ.setdefault("HARBOR_HOME", _scratch.name)

import service.tools  # noqa: E402,F401  (registers every tool)
import service.router.router as R  # noqa: E402
from service.tools.registry import REGISTRY  # noqa: E402


def subset(prompt: str) -> list[str]:
    decision = R._domain_subset(prompt, [])
    assert decision is not None, prompt
    return list(decision.tool_subset or [])


def test_all_sources_are_registered_and_include_the_raw_readers():
    assert set(R._ALL_SOURCES) <= set(REGISTRY)
    assert {"view_emails", "view_messages", "triage_inbox", "get_recent_activity"} <= set(R._ALL_SOURCES)
    assert set(R._CORE_SOURCES) <= set(R._ALL_SOURCES)


def test_aggregate_route_offers_raw_readers_but_waits_only_on_core_sources():
    decision = R._domain_subset("help me plan my day", [])
    assert {"view_emails", "view_messages", "triage_inbox"} <= set(decision.tool_subset)
    assert set(decision.narration_after) == set(R._CORE_SOURCES)


def test_email_domain_offers_attachment_reader():
    assert "read_email_attachments" in subset("check my email from the school")


def test_message_read_domain_offers_attachments_and_past_conversations():
    tools = subset("any texts from sam")
    assert {"read_message_attachments", "search_conversations"} <= set(tools)
    assert not any("email" in t for t in tools)        # a named channel is not widened


def test_calendar_domain_includes_reminders():
    assert "search_reminders" in subset("when am I free this week")


def test_reminder_read_survives_a_calendar_mention():
    decision = R._domain_subset("show my reminders and my calendar for today", [])
    assert decision.force_first_tool == "search_reminders"
    assert {"search_reminders", "get_upcoming"} <= set(decision.tool_subset)
    assert frozenset({"search_reminders"}) in decision.required_tool_groups
    assert frozenset({"get_upcoming"}) in decision.required_tool_groups


def test_a_reminder_only_read_is_unchanged():
    decision = R._domain_subset("what reminders do i have", [])
    assert decision.tool_subset == ["search_reminders"]


def test_recency_route_lists_each_tool_once():
    decision = R._mk_scoped(list(dict.fromkeys(["get_recent_activity", *R._ALL_SOURCES])), "x")
    assert len(decision.tool_subset) == len(set(decision.tool_subset))


_PHANTOMS = ("search_memory", "clear_memory", "unsubscribe", "scan_to_note", "switch_app",
             "place_call", "http_request")


def test_phantom_names_are_not_registered_and_never_offered():
    # They stay only in deny lists and safety latches, where a connected or
    # user-made tool of that name must still be refused or latched.
    assert not set(_PHANTOMS) & set(REGISTRY)
    for name in _PHANTOMS:
        assert name not in R._ALL_SOURCES
    src = Path(R.__file__).read_text()
    assert not re.search(r"_mk_scoped\(\[[^\]]*\"(?:%s)\"" % "|".join(_PHANTOMS), src)


def test_attention_read_instruction_names_every_source_it_covers():
    decision = R._priority_read("what needs my attention in my email and messages?")
    assert decision is not None
    text = decision.resolved_request
    assert "communication sources" not in text
    assert "(mail, messages, notes, reminders)" in text
    assert "says nothing about Mail, Notes or Reminders" in text
