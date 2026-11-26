"""A Mail body snapshot one test publishes is gone before the next test (tests/conftest.py).

Synthetic coverage only; nothing is written to disk."""
from __future__ import annotations

from service.tools import email_tools as mail

_seen: dict = {}


def test_a_test_publishes_a_body_snapshot(monkeypatch):
    monkeypatch.setattr(mail.cache_store, "save", lambda *a: None)
    _seen["before"] = mail._raw_mail_snapshot
    mail.cache_raw_emails("", {"accounts": ["Work"], "failed_accounts": [], "complete": True})
    assert mail._raw_mail_snapshot is not None and mail._raw_mail_snapshot is not _seen["before"]


def test_the_next_test_starts_from_what_was_there_before():
    assert "before" in _seen                           # runs after the test above
    assert mail._raw_mail_snapshot is _seen["before"]
