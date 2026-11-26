"""Candidate review #4: the action log rotates when it grows past its cap and keeps
several older generations, so one burst of actions can't push the record of an
earlier one out of existence, and the log can't grow without bound. Invented
events in a temp folder only.
"""
from __future__ import annotations

import importlib

import pytest

# The package exports the audit() function under the same name; this is the module.
audit = importlib.import_module("service.safety.audit")


@pytest.fixture
def log(tmp_path, monkeypatch):
    path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(audit, "AUDIT_LOG", path)
    monkeypatch.setattr(audit, "ROTATE_BYTES", 2048)
    return path


def test_rotation_keeps_several_private_generations(log):
    for i in range(400):
        audit.audit("fixture_event", n=i, note="x" * 40)
    generations = sorted(log.parent.glob("audit.jsonl.*"))
    assert len(generations) == audit.KEEP_GENERATIONS
    for path in [log, *generations]:
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.stat().st_size <= 2048 + 200
    # The oldest kept generation still holds events written before the newest burst.
    assert '"n": ' in (log.parent / f"audit.jsonl.{audit.KEEP_GENERATIONS}").read_text()


def test_a_link_in_place_of_a_generation_is_not_followed(log, tmp_path):
    elsewhere = tmp_path / "elsewhere.txt"
    elsewhere.write_text("untouched")
    (tmp_path / "audit.jsonl.2").symlink_to(elsewhere)
    for i in range(200):
        audit.audit("fixture_event", n=i, note="x" * 40)
    assert elsewhere.read_text() == "untouched"
