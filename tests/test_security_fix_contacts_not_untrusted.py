"""Simulation QA follow-up to #22: looking up the user's own Contacts on this Mac
is not an untrusted read, so "Text Mom saying hello; remind me to buy milk" gets one
card, for the text. Mail, Messages bodies, web pages, documents, calendar text and
Study pages still count. Invented arguments; nothing runs.
"""
from __future__ import annotations

import pytest

import service.tools  # noqa: F401 - registers every tool
from service.safety import policy
from service.tools.registry import REGISTRY


def _reads(name: str, args: dict | None = None) -> bool:
    tool = REGISTRY[name]
    return policy.turn_reads_untrusted(tool.name, tool.category, args or {})


@pytest.mark.parametrize("name", ["lookup_contact", "list_contacts", "contact_dates"])
def test_a_contacts_lookup_is_the_users_own_data(name):
    assert not _reads(name, {"name": "Mom"})


@pytest.mark.parametrize("name", ["view_emails", "summarize_messages", "web_fetch", "web_search",
                                  "read_file", "get_upcoming", "get_past_events", "study_slides"])
def test_outside_text_still_sets_the_latch(name):
    assert _reads(name)


def test_after_only_a_contacts_lookup_a_reminder_follows_full_access(monkeypatch):
    monkeypatch.setattr(policy, "_FULL_ACCESS", True)
    args = {"title": "buy milk", "when_iso": "2026-11-26T09:00"}
    read_untrusted = _reads("lookup_contact", {"name": "Mom"})
    decision = policy.decide("assistant_write", args, tool="add_reminder")
    if read_untrusted:
        decision = policy.after_untrusted_read_decision("add_reminder", "assistant_write", args, decision)
    assert decision.tier is policy.Tier.ALLOW
