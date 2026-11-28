"""Build an mlx-lm LoRA dataset from real Harbor usage.

Turns sessions.db (user/assistant text) into mlx-lm's chat-format JSONL:
{"messages": [...]} per line. This is real router/agent behavior on this
user's actual traffic, not synthetic data.

Assistant turns that ran tools are kept in the history but are not used as
training targets: audit.jsonl records the call and its arguments but never the
tool's result, so the turn cannot be rebuilt as call -> tool result -> answer,
and a `tool_calls` message with no tool reply after it is not a valid
conversation. Tool-calling examples come from harvest_training_data.py, which
captures the real model input and output.

Splits are by session: every example from one session lands in the same file,
so a valid example never shares its history with a train example.

Usage:
    python scripts/build_lora_dataset.py --out ~/lora_data --val-frac 0.1
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path
from urllib.parse import quote

from service.agent.loop import SYSTEM
from service.router.router import route
from service.tools import tool_schemas

SESSIONS_DB = Path.home() / ".moe" / "sessions.db"

MIN_ASSISTANT_CHARS = 8   # drop near-empty / truncated turns
MAX_MESSAGE_CHARS = 4000  # drop runaway turns (bad for a 2.6B context budget)


def _private_mkdirs(path: Path) -> None:
    """Create `path` and every missing parent, each 0700.

    mkdir(mode=..., parents=True) gives the mode only to the last directory;
    the parents it creates get the umask's (0755). Directories that already
    exist are left as they are.
    """
    missing = []
    while not os.path.lexists(path):
        missing.append(path)
        path = path.parent
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            pass


def _private_dir(path: Path) -> Path:
    """The output directory, created owner-only (0700) when it is new.

    An existing directory is left as it is (`--out ~` must not strip access
    from the home folder); the files written into it are 0600 either way.
    """
    if not path.is_dir():
        _private_mkdirs(path)
    elif path.stat().st_mode & 0o077:
        print(f"note: {path} is readable by other accounts; the files written "
              "there are owner-only", file=sys.stderr)
    return path


def _write_private(path: Path, text: str) -> None:
    """Write `text` to a file that is 0600 from its first byte.

    The examples are the user's own chat turns from sessions.db (with real
    tool arguments), so they get the store's owner-only mode instead of the
    process umask. fchmod also tightens a file an earlier run left 0644.
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        f = os.fdopen(fd, "w")
    except BaseException:
        os.close(fd)              # fdopen did not take ownership of it
        raise
    with f:
        f.write(text)


async def build_examples(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.execute(
        "SELECT session_id, idx, role, content, tool_digest, created_at "
        "FROM turns ORDER BY session_id, idx"
    )
    rows = cur.fetchall()

    by_session: dict[str, list[tuple]] = {}
    for row in rows:
        by_session.setdefault(row[0], []).append(row)

    examples = []
    for sid, turns in by_session.items():
        messages = [{"role": "system", "content": SYSTEM}]
        last_user_text = None
        for _, idx, role, content, tool_digest, created_at in turns:
            if not content or len(content) > MAX_MESSAGE_CHARS:
                continue
            if role == "assistant" and len(content) < MIN_ASSISTANT_CHARS:
                continue

            msg = {"role": role, "content": content}

            if role == "user":
                last_user_text = content

            messages.append(msg)

            if role == "assistant" and tool_digest:
                # The tools ran but their results were never recorded (see the
                # module docstring), so this turn is history only, not a target.
                skipped_tool_turns[0] += 1
                continue

            # emit one training example per completed assistant turn, using
            # everything up to and including it as the target
            if role == "assistant":
                # Re-run the SAME routing logic that decided the tool subset at
                # SERVE time, on the user text that triggered this turn — the
                # audit log records which tool ran, never what subset it was
                # offered from, so reconstructing it any other way would be a
                # guess. Without this, the model trains on a `tools`-less
                # prompt shape it will never actually see at inference,
                # exactly the mismatch harvest_training_data.py's raw_model_io
                # capture avoids. See moe-context-window-budget /
                # moe-tool-subset-routing.
                tools = None
                if last_user_text:
                    decision = await route(last_user_text)
                    if decision.tool_subset:
                        tools = tool_schemas(decision.tool_subset)
                examples.append({"messages": [m.copy() for m in messages], "tools": tools,
                                 "_session": sid})

    return examples


skipped_tool_turns = [0]


def split_by_session(examples: list[dict], val_frac: float) -> tuple[list[dict], list[dict]]:
    """(train, valid) with whole sessions on one side, chosen by a stable hash."""
    sessions: dict[str, list[dict]] = {}
    for example in examples:
        sessions.setdefault(example["_session"], []).append(example)
    order = sorted(sessions, key=lambda sid: hashlib.sha256(sid.encode()).hexdigest())
    want = max(1, int(len(examples) * val_frac)) if examples else 0
    train: list[dict] = []
    valid: list[dict] = []
    for sid in order:
        (valid if len(valid) < want and len(order) > 1 else train).extend(sessions[sid])
    return train, valid


def _public(example: dict) -> dict:
    return {k: v for k, v in example.items() if not k.startswith("_")}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="~/lora_data")
    ap.add_argument("--val-frac", type=float, default=0.1)
    args = ap.parse_args()

    out_dir = _private_dir(Path(args.out).expanduser())

    # Read-only: a missing store must not be created here with the umask.
    # Percent-encoded: a ? # or % in the path must not change which file opens.
    conn = sqlite3.connect("file:" + quote(str(SESSIONS_DB)) + "?mode=ro", uri=True)
    examples = await build_examples(conn)

    train, val = split_by_session(examples, args.val_frac)

    _write_private(out_dir / "train.jsonl",
                   "\n".join(json.dumps(_public(e)) for e in train) + "\n")
    _write_private(out_dir / "valid.jsonl",
                   "\n".join(json.dumps(_public(e)) for e in val) + "\n")
    # mlx_lm.lora requires a test.jsonl to exist even if --test isn't passed
    _write_private(out_dir / "test.jsonl",
                   "\n".join(json.dumps(_public(e)) for e in val[: max(1, len(val) // 2)]) + "\n")

    with_subset = sum(1 for e in examples if e.get("tools"))
    sessions = len({e["_session"] for e in examples})
    print(f"{len(examples)} examples from {sessions} sessions ({with_subset} with a routed "
          f"tool subset; {skipped_tool_turns[0]} tool-using turns kept as history only) "
          f"-> {len(train)} train / {len(val)} valid, written to {out_dir}")


if __name__ == "__main__":
    asyncio.run(main())
