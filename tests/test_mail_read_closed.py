"""Mail closed: the freshness window, the Brief's one-sentence cause and the Swift
sentence it shares. Intel since 0.1.8: Mail's on-disk index is not counted as current
(Apple silicon's rule); a live read comes from Mail opened hidden (MailRecache)."""
from __future__ import annotations

import asyncio
import re
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ROOT = Path(__file__).resolve().parents[1]
SWIFT = ROOT / "app" / "Sources" / "HarborApp"

from service.tools import cache_store, email_tools as E

FDA_SENTENCE = ("Harbor can't read Apple Mail: open Mail once, or allow Full Disk Access for Harbor in "
                "System Settings > Privacy & Security.")


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    scratch = tempfile.TemporaryDirectory(prefix="harbor-mail-closed-")
    monkeypatch.setattr(cache_store, "CACHE_DIR", Path(scratch.name))
    monkeypatch.setattr(cache_store, "save", lambda *a, **k: None)
    for name, value in dict(_headers="", _history="", _raw_emails="", _swift_headers="", _swift_history="",
                            _swift_raw="", _external_accounts={}, _headers_sync_generation=0,
                            _email_available=None, _email_read_source="", _email_reason="",
                            _email_reason_kind="", _email_sync_pending=False, _native_headers_at=0.0,
                            _native_raw_at=0.0, _native_history_at=0.0, _native_source="",
                            _mail_automation_refused=False).items():
        monkeypatch.setattr(E, name, value)
    yield
    scratch.cleanup()


def read_mail(source: str, *, age: float = 0.0, history: bool = False):
    now = time.time()
    row = f"{now - 3600} | Home | Sender | Subject"
    E.cache_emails(row)
    if history:
        E.cache_history_headers(row)
    E.set_email_availability(True, read_source=source)
    E._native_headers_at = now - age
    if history:
        E._native_history_at = now - age


def headers_row():
    return E.email_source_details(part="headers")[0]


# --- freshness window ---------------------------------------------------------

def test_window_has_margin_over_every_timer():
    timer = int(re.search(r"withTimeInterval: (\d+), repeats: true\) \{ _ in\s+self\?\.sync\(\)",
                          (SWIFT / "MailReader.swift").read_text()).group(1))
    assert timer == E._HEADERS_INTERVAL == 300
    assert E._EXTERNAL_MAX_AGE >= 2 * timer
    assert E._EXTERNAL_PART_MAX_AGE["headers"] == E._EXTERNAL_MAX_AGE   # Gmail and Mail agree
    assert E._SYNC_REQUEST_AGE == timer          # a question still asks for mail newer than one interval


@pytest.mark.parametrize("source", ["mail_app", "mail_fetch"])
def test_a_late_sync_still_reads_current_and_a_missed_one_does_not(source):
    read_mail(source, age=E._HEADERS_INTERVAL + 90)     # a tick 90 s late
    assert headers_row()["fresh"] is True
    read_mail(source, age=E._EXTERNAL_MAX_AGE + 5)
    assert headers_row()["fresh"] is False


# --- reads that are not current -------------------------------------------------

def test_a_failed_index_read_is_not_current():
    read_mail("local_index", age=30)
    E.set_email_availability(False, FDA_SENTENCE, reason_kind="full_disk_access")
    assert headers_row()["fresh"] is False


@pytest.mark.parametrize("age, asks", [(100, False), (E._SYNC_REQUEST_AGE + 60, True)])
def test_a_question_asks_for_new_mail_only_after_one_interval(age, asks):
    read_mail("mail_app", age=age)
    seen = []

    async def publish(event):
        seen.append(event)
    with patch("service.assistant.hub.hub.publish", publish):
        asyncio.run(E._ensure_email_cache(timeout_seconds=0))
    assert bool(seen) is asks


# --- the Brief's cause and fix ---------------------------------------------------

def test_brief_names_the_cause_in_one_sentence_with_its_fix_kind():
    from service.assistant import daily_workspace
    E.set_email_availability(False, FDA_SENTENCE, reason_kind="full_disk_access")
    result = {"email": {"ready": False}}
    daily_workspace._annotate_access(result, time.time())
    info = result["email"]
    assert info["reason"] == FDA_SENTENCE and info["blocked"] == "full_disk_access"
    assert info["state"] == "unavailable"
    assert FDA_SENTENCE.count(".") == 1 and "\n" not in FDA_SENTENCE


# --- Swift contract ---------------------------------------------------------------
# Where Mail may be opened hidden is MailRecache's (tests/MailRecacheChecks.swift).

def test_swift_sentence_matches_the_service_text():
    assert FDA_SENTENCE in (SWIFT / "MailDBReader.swift").read_text().replace('"\n        + "', "")
