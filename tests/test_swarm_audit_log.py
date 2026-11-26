"""Swarm #37: audit.jsonl was never rotated, and GET /audit read the whole file on
the event loop. It now rotates at a size limit and the tail is read backwards, off
the loop. Scratch files only.
"""
from __future__ import annotations

import asyncio
import importlib
import json

from service import main

audit_mod = importlib.import_module("service.safety.audit")  # not service.safety's audit() function


def test_the_audit_log_rotates_at_its_limit(tmp_path, monkeypatch):
    log = tmp_path / "audit.jsonl"
    monkeypatch.setattr(audit_mod, "AUDIT_LOG", log)
    monkeypatch.setattr(audit_mod, "MAX_BYTES", 400)
    for i in range(40):
        audit_mod.audit("tool", tool="example_tool", n=i)
    assert log.stat().st_size < 400 + 200, "the live file stays near the limit"
    older = audit_mod.rotated(log)
    assert older.exists() and older.stat().st_mode & 0o077 == 0
    # One older generation only: nothing else piles up beside it.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["audit.jsonl", "audit.jsonl.1"]
    # The newest lines come back in order, across the rotation when needed.
    live = len(log.read_text().splitlines())
    want = live + 2    # reaches back into the rotated file
    tail = [json.loads(line)["n"] for line in audit_mod.tail_lines(want, log)]
    assert tail == list(range(40 - want, 40))


def test_tail_reads_backwards_and_skips_a_line_cut_by_its_block(tmp_path):
    log = tmp_path / "audit.jsonl"
    log.write_text("".join(json.dumps({"n": i, "pad": "x" * 30}) + "\n" for i in range(500)))
    tail = audit_mod.tail_lines(5, log, block=128)
    assert [json.loads(line)["n"] for line in tail] == [495, 496, 497, 498, 499]
    assert audit_mod.tail_lines(0, log) == []
    assert audit_mod.tail_lines(3, tmp_path / "missing.jsonl") == []


def test_get_audit_reads_the_tail_off_the_loop(tmp_path, monkeypatch):
    log = tmp_path / "audit.jsonl"
    monkeypatch.setattr(audit_mod, "AUDIT_LOG", log)
    for i in range(10):
        audit_mod.audit("tool", tool="example_tool", n=i)
    log.write_text(log.read_text() + "{torn line\n")
    used = []
    real = asyncio.to_thread

    async def watched(func, *args, **kwargs):
        used.append(func)
        return await real(func, *args, **kwargs)

    monkeypatch.setattr(main.asyncio, "to_thread", watched)
    result = asyncio.run(main.get_audit(limit=3))
    assert [entry["n"] for entry in result["entries"]] == [9, 8, 7], "newest first; a torn line is skipped"
    assert audit_mod.tail_lines in used
    text = open(main.__file__).read()
    route = text[text.index("async def get_audit("):text.index("# Smart Search")]
    assert "read_text(" not in route
