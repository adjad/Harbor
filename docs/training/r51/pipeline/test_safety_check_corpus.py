"""safety_check_corpus.py on invented rows: tool-call steps and finals, every family.

Run: python3 -m pytest test_safety_check_corpus.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import safety_check_corpus as C  # noqa: E402

_, IS_WRITE = C._write_tools()


def call(name, **args):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": name, "arguments": args}}]}


def final(text):
    return {"role": "assistant", "content": text}


def rules(target, **meta):
    meta = {"id": "x-000-s0", "family": meta.pop("family", "any_family"), **meta}
    row = {"messages": [{"role": "user", "content": "hi"}, target]}
    return [f["rule"] for f in C.check_row(row, meta, IS_WRITE)]


def test_a_licensed_write_passes_and_an_unlicensed_one_is_flagged_in_any_family():
    assert rules(call("add_reminder", title="Dentist"), writes=["add_reminder"]) == []
    assert rules(call("add_reminder", title="Dentist"), family="r5_final_family", writes=[]) == ["write_not_licensed"]
    assert rules(call("add_reminder", title="Dentist"), writes=[], turn_writes=["add_reminder"]) == []


def test_a_preview_is_a_read():
    assert rules(call("organize_files", pattern="*.xlsx", folder="~/Documents"), writes=[]) == []
    got = rules(call("organize_files", pattern="*.xlsx", folder="~/Documents", confirm=True), writes=[])
    assert got == ["write_not_licensed"]


def test_reads_are_never_flagged():
    assert rules(call("get_upcoming", days=1), writes=[], gold={"intent": "leave_alone"}) == []


def test_no_write_intents_flag_any_write_but_refused_may_attempt_a_licensed_one():
    for intent in ("leave_alone", "clarify", "read_only", "embedded_instruction"):
        got = rules(call("send_message", to="Ana", body="ok"), writes=["send_message"], gold={"intent": intent})
        assert got == ["write_on_no_write_turn"], intent
    assert rules(call("cancel_event", title="Lab meeting"), writes=["cancel_event"], gold={"intent": "refused"}) == []


def test_a_draft_turn_must_not_send():
    got = rules(call("send_email", to="ana@example.com", body="hi"), writes=["send_email"], gold={"intent": "draft"})
    assert got == ["draft_calls_send"]


def test_final_text_rules_apply_to_any_family_with_an_intent():
    assert rules(final("I've sent it to Ana."), family="r5_final_family",
                 gold={"intent": "leave_alone"}) == ["claims_action_on_no_write_turn"]
    assert rules(final("Sounds good."), gold={"intent": "clarify"}) == ["clarify_asks_nothing"]
    assert rules(final("Which Ana did you mean?"), gold={"intent": "clarify"}) == []


def test_a_clarify_turn_without_an_intent_must_ask_and_not_claim_an_action():
    assert rules(final("I've just activated Do Not Disturb for you."), clarify=True) == ["claims_action_on_clarify_turn"]
    assert rules(final("Happy to check, just let me know which city."), clarify=True) == []
    assert rules(final("I'll need to know which location you mean!"), clarify=True) == []
    assert rules(final("It will be sunny."), clarify=True) == ["clarify_asks_nothing"]


def test_main_counts_and_exit_code(tmp_path, capsys):
    rows, metas = tmp_path / "train.jsonl", tmp_path / "train.meta.jsonl"
    good = ({"messages": [{"role": "user", "content": "x"}, call("add_reminder", title="a")]},
            {"id": "a-0-s0", "family": "a", "writes": ["add_reminder"]})
    bad = ({"messages": [{"role": "user", "content": "y"}, call("add_reminder", title="b")]},
           {"id": "b-0-s0", "family": "b", "writes": []})
    rows.write_text("\n".join(json.dumps(r) for r, _ in (good, bad)) + "\n")
    metas.write_text("\n".join(json.dumps(m) for _, m in (good, bad)) + "\n")
    out = tmp_path / "found.jsonl"
    assert C.main([str(rows), str(metas), "--out", str(out)]) == 1
    summary = json.loads(capsys.readouterr().out)
    assert summary["rows"] == 2 and summary["flagged"] == 1 and summary["by_family"] == {"b": 1}
    assert [json.loads(x)["id"] for x in out.read_text().splitlines()] == ["b-0-s0"]
    metas.write_text("\n".join(json.dumps(m) for _, m in (good, good)) + "\n")
    rows.write_text("\n".join(json.dumps(r) for r, _ in (good, good)) + "\n")
    assert C.main([str(rows), str(metas)]) == 0
