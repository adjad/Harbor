"""Train/valid splits for the LoRA dataset scripts keep conversations whole.

Invented sessions only; no store, model or user data is read.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sqlite3
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _turns(first: str, n: int) -> list[dict]:
    """n examples from one conversation: each one's history grows by a turn."""
    out, messages = [], [{"role": "user", "content": first}]
    for i in range(n):
        messages = messages + [{"role": "assistant", "content": f"answer {i}"}]
        out.append({"messages": list(messages)})
        messages = messages + [{"role": "user", "content": f"follow-up {i}"}]
    return out


def _keys(merge, rows):
    return {merge._conversation(r) for r in rows}


def test_merge_split_keeps_each_conversation_on_one_side_and_both_sources_in_valid():
    merge = _load("merge_lora_data")
    harvest = [e for k in range(20) for e in _turns(f"harvest prompt {k}", 3)]
    real = [e for k in range(10) for e in _turns(f"real prompt {k}", 2)]
    train, valid = merge.stratified_split([harvest, real], 0.2)
    assert len(train) + len(valid) == len(harvest) + len(real)
    assert not _keys(merge, train) & _keys(merge, valid)
    firsts = {r["messages"][0]["content"].split(" prompt")[0] for r in valid}
    assert firsts == {"harvest", "real"}
    again = merge.stratified_split([harvest, real], 0.2)
    assert (train, valid) == again                                # deterministic


def _store(path: Path, rows):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE turns (session_id TEXT, idx INTEGER, role TEXT, content TEXT,"
               " tool_digest TEXT, display_content TEXT, display_kind TEXT, created_at REAL)")
    db.executemany("INSERT INTO turns (session_id, idx, role, content, tool_digest, created_at)"
                   " VALUES (?, ?, ?, ?, ?, ?)", rows)
    db.commit()
    db.close()


def test_build_never_emits_an_unanswered_tool_call_and_splits_by_session(tmp_path, monkeypatch):
    build = _load("build_lora_dataset")
    rows = []
    for s in range(12):
        sid = f"s{s}"
        rows += [(sid, 0, "user", f"what's on today, {sid}", None, 1.0),
                 (sid, 1, "assistant", "Two meetings and a dentist visit.", "get_upcoming", 2.0),
                 (sid, 2, "user", "thanks, and tomorrow?", None, 3.0),
                 (sid, 3, "assistant", "Nothing booked tomorrow yet.", None, 4.0),
                 (sid, 4, "user", "great", None, 5.0),
                 (sid, 5, "assistant", "Glad to help.", None, 6.0)]
    store = tmp_path / "sessions.db"
    _store(store, rows)

    async def no_route(text):
        return types.SimpleNamespace(tool_subset=None)

    monkeypatch.setattr(build, "route", no_route)
    monkeypatch.setattr(build, "SESSIONS_DB", store)
    out = tmp_path / "lora"
    monkeypatch.setattr("sys.argv", ["build_lora_dataset.py", "--out", str(out), "--val-frac", "0.25"])
    asyncio.run(build.main())
    train = [json.loads(x) for x in (out / "train.jsonl").read_text().splitlines() if x]
    valid = [json.loads(x) for x in (out / "valid.jsonl").read_text().splitlines() if x]
    every = train + valid
    assert len(every) == 24                                       # 2 text-only targets per session
    for example in every:
        assert set(example) <= {"messages", "tools"}              # no internal keys written
        assert not any("tool_calls" in m for m in example["messages"])
        assert example["messages"][-1]["content"] != "Two meetings and a dentist visit."
    train_prefixes = {json.dumps(e["messages"][:4]) for e in train}
    assert valid and not any(json.dumps(e["messages"][:4]) in train_prefixes for e in valid)
