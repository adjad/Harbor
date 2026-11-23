"""While Harbor reads a Mail it opened because Google isn't covering mail (MailRecache.swift), Brief
says "Reading Mail…" and never that there is no mail. Synthetic state only: no Mail, no network."""
from __future__ import annotations

import os
import tempfile

_scratch = tempfile.TemporaryDirectory(prefix="harbor-mail-recache-tests-")
os.environ.setdefault("HARBOR_HOME", _scratch.name)

from pathlib import Path  # noqa: E402

from service.assistant import brief as B  # noqa: E402
from service.tools import email_tools as E  # noqa: E402

NOW = 1_790_000_000.0


def _state(monkeypatch, *, pending, kind, mail_state="syncing"):
    monkeypatch.setattr(E, "_email_sync_pending", pending)
    monkeypatch.setattr(E, "_email_reason_kind", kind)
    monkeypatch.setattr(E, "email_sync_state", lambda: mail_state)
    monkeypatch.setattr(B, "_mail_split", lambda now: {"state": mail_state, "rows": [], "people": [],
                                                       "automated": [], "label": "today"})


def test_reading_mail_only_with_the_recache_reason(monkeypatch):
    _state(monkeypatch, pending=True, kind=E.READING_MAIL_KIND)
    assert E.reading_mail() is True
    _state(monkeypatch, pending=True, kind="")
    assert E.reading_mail() is False
    _state(monkeypatch, pending=False, kind=E.READING_MAIL_KIND)
    assert E.reading_mail() is False


def test_brief_says_reading_mail_and_never_empty(monkeypatch):
    _state(monkeypatch, pending=True, kind=E.READING_MAIL_KIND)
    section = B._email_section(NOW)
    assert section == "**📧 Inbox**\n- Reading Mail…"
    block = B._email_block(NOW)
    assert "reading Mail" in block and "never call the inbox empty" in block
    for text in (section, block):
        assert "No messages" not in text and "couldn't be read" not in text.lower()


def test_plain_launch_sync_keeps_its_old_words(monkeypatch):
    _state(monkeypatch, pending=True, kind="")
    assert B._email_section(NOW) == "**📧 Inbox**\n- Mail is still syncing; ask again in a moment."
    assert B._email_block(NOW) == "EMAIL: Harbor is still syncing mail after launch."


def test_the_swift_side_sends_the_same_kind_and_words():
    swift = (Path(__file__).resolve().parents[1] / "app/Sources/HarborApp/MailRecache.swift").read_text()
    assert f'static let readingKind = "{E.READING_MAIL_KIND}"' in swift
    assert 'static let readingNote = "Reading Mail…"' in swift


def test_mail_reader_never_asks_and_never_forces():
    src = (Path(__file__).resolve().parents[1] / "app/Sources/HarborApp/MailReader.swift").read_text()
    start = src.index("// MARK: - Opening Mail when Google isn't covering it")
    block = src[start:src.index("// MARK: - Scans", start)]
    assert "forceTerminate" not in block and "kill(" not in block
    assert "app.terminate()" in block
    # The permission probe never asks (askUserIfNeeded false), and nothing in the block asks macOS.
    # (bug sweep #16: the probe returns macOS's status, so "not running" can fall back to the last answer)
    assert "AEDeterminePermissionToAutomateTarget(&target, AEEventClass(typeWildCard),\n" \
           "                                                     AEEventID(typeWildCard), false)" in block
    for asking in ("requestAccess", "AXIsProcessTrustedWithOptions", "Permissions.ask", ", true)"):
        assert asking not in block, asking
    assert "config.activates = false" in block and "config.hides = true" in block \
        and "config.addsToRecentItems = false" in block
