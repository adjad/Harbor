#!/usr/bin/env python3
"""Safety check over a whole finished training set: every family, tool-call steps and final text.

    safety_check_corpus.py SET/train.jsonl SET/train.meta.jsonl [ROWS META ...] [--out report.jsonl]

safety_check_r51.py judges only the safety_contrast_r51 finals. This reads every row of every family (r5-final
included) with its meta line and flags:
  - write_not_licensed       the trained target makes a write call the row's meta does not allow (meta.writes or
                             meta.turn_writes). A preview is a read: organize_files / move_path without confirm=true;
  - write_on_no_write_turn   gold.intent says nothing should be done this turn (leave_alone, clarify, read_only,
                             embedded_instruction) and the target makes any write call. (refused is not here: a
                             refused row trains the licensed attempt the tool then refuses; its final is still
                             held to "claims no action" by rule_for);
  - draft_calls_send         gold.intent is draft and the target calls a sending tool;
  - the final-text rules of safety_check_r51.rule_for, for every final whose row has a gold.intent;
  - for rows without a gold.intent whose meta.clarify is true (the turn should ask, not act):
      claims_action_on_clarify_turn   the final says something was done ("I've just activated ...");
      clarify_asks_nothing            the final neither asks a question nor asks to be told ("let me know which city").
Rows without a gold.intent still get the two tool-call checks and the clarify check.

Writes one JSON line per finding (id, family, rule, tool) to --out, prints counts by rule and family, and exits 1
when anything is flagged, so a pipeline can stop on it. Ids and rule names only: no row text is printed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "r2", "builder"))

from safety_check_r51 import DONE, NO_WRITE, rule_for  # noqa: E402

SENDS = frozenset({"send_email", "send_message", "reply_to_email", "schedule_send", "forward_email"})
PREVIEW_FIRST = frozenset({"organize_files", "move_path"})       # without confirm=true these only preview
NO_CALL_WRITE = NO_WRITE - {"refused"}
# DONE plus the settings verbs it lacks ("I've just activated Do Not Disturb"); safety_check_r51 keeps its own
# DONE unchanged so the recorded r5.1 build stays reproducible.
DONE_ANY = re.compile(DONE.pattern + r"|\bi(?:'ve| have| just)?\s+(?:just\s+)?(activated|turned (?:on|off)|enabled|disabled|"
                      r"muted|unmuted|silenced|switched (?:on|off))\b", re.I)
ASKS = re.compile(r"\?|\b(let me know|tell me|(?:need|have) to know|could you(?: please)?|if you could|which (?:one|city|day|time))\b",
                  re.I)


def _write_tools():
    import thinking  # the builder's own list, so the check and the builder agree on what a write is
    return thinking.WRITE_TOOLS, thinking.is_write_call


def _args(call: dict) -> dict:
    raw = (call.get("function") or {}).get("arguments")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            got = json.loads(raw)
            return got if isinstance(got, dict) else {}
        except ValueError:
            return {}
    return {}


def is_write(name: str, args: dict, is_write_call) -> bool:
    if not is_write_call(name, args):
        return False
    return not (name in PREVIEW_FIRST and args.get("confirm") is not True)


def check_row(row: dict, meta: dict, is_write_call) -> list[dict]:
    target = (row.get("messages") or [{}])[-1]
    if target.get("role") != "assistant":
        return []
    intent = (meta.get("gold") or {}).get("intent")
    allowed = set(meta.get("writes") or ()) | set(meta.get("turn_writes") or ())
    out = []

    def flag(rule, tool=None):
        out.append({"id": meta.get("id"), "family": meta.get("family"), "rule": rule, **({"tool": tool} if tool else {})})

    calls = target.get("tool_calls") or []
    for call in calls:
        name = (call.get("function") or {}).get("name") or ""
        args = _args(call)
        if not is_write(name, args, is_write_call):
            continue
        if name not in allowed:
            flag("write_not_licensed", name)
        if intent in NO_CALL_WRITE:
            flag("write_on_no_write_turn", name)
        if intent == "draft" and name in SENDS:
            flag("draft_calls_send", name)
    if not calls:
        text = target.get("content") or ""
        if intent:
            rule = rule_for(intent, text)
            if rule:
                flag(rule)
        if meta.get("clarify") is True and intent is None:
            if DONE_ANY.search(text):
                flag("claims_action_on_clarify_turn")
            elif not ASKS.search(text):
                flag("clarify_asks_nothing")
    return out


def check_files(rows_path: str, meta_path: str, is_write_call) -> tuple[list[dict], Counter]:
    findings, seen = [], Counter()
    with open(rows_path) as rows, open(meta_path) as metas:
        for n, (r, m) in enumerate(zip(rows, metas, strict=True), 1):
            row, meta = json.loads(r), json.loads(m)
            seen[meta.get("family")] += 1
            findings += check_row(row, meta, is_write_call)
    return findings, seen


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("pairs", nargs="+", help="ROWS.jsonl META.jsonl [ROWS META ...]")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    if len(a.pairs) % 2:
        ap.error("give rows and meta files in pairs")
    _, is_write_call = _write_tools()
    findings, seen = [], Counter()
    for rows_path, meta_path in zip(a.pairs[::2], a.pairs[1::2]):
        f, s = check_files(rows_path, meta_path, is_write_call)
        findings += f
        seen += s
    if a.out:
        with open(a.out, "w") as f:
            for x in findings:
                f.write(json.dumps(x) + "\n")
    print(json.dumps({"rows": sum(seen.values()), "families": len(seen), "flagged": len(findings),
                      "by_rule": dict(Counter(x["rule"] for x in findings)),
                      "by_family": dict(Counter(x["family"] for x in findings))}))
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
