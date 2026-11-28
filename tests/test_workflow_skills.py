"""The four bundled workflow skills: inbox-cleanup, meeting-prep, week-review, file-tidy.

Invented data and fake tools only. A workflow skill is a fixed script of calls to
Harbor's own tools, declared in its SKILL.md `contract`; the model fills arguments.
These tests run each declared script through the REAL policy engine with a fake
executor that records every call, so a skill that moved, archived or sent
something without a confirm, or a tool whose policy later got looser, fails here.

Nothing here touches a mailbox, calendar, reminders or any real folder.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

import service.main  # noqa: F401 — registers every built-in tool
from service import skills
from service.safety import grants, policy
from service.skills import contracts
from service.tools.registry import REGISTRY

WORKFLOWS = ("inbox-cleanup", "meeting-prep", "week-review", "file-tidy")
READ_ONLY_SKILLS = ("meeting-prep", "week-review")
CONFIRMING_SKILLS = ("inbox-cleanup", "file-tidy")
# Tools that change something. organize_files only when it is called with confirm=true.
MUTATING = {"email_triage", "organize_files", "move_path", "write_file", "send_email", "send_message",
            "reply_to_email", "forward_email", "schedule_send", "draft_email", "draft_message",
            "add_calendar_event", "update_event", "cancel_event", "add_reminder", "update_reminder",
            "complete_reminder", "clear_reminders", "create_note", "append_note", "delete_note"}


@pytest.fixture(autouse=True)
def default_mode(monkeypatch, tmp_path):
    """Default access mode, no standing grants, whatever this Mac has saved."""
    monkeypatch.setattr(policy, "_FULL_ACCESS", False, raising=False)
    monkeypatch.setattr(policy, "_READ_ONLY", False, raising=False)
    monkeypatch.setattr(grants, "GRANTS_PATH", tmp_path / "grants.json")
    monkeypatch.setattr(grants, "_CACHE", {})


@pytest.fixture()
def loaded(tmp_path, monkeypatch):
    original = skills.all_skills()
    monkeypatch.setattr(skills, "SKILLS_DIR", tmp_path / "user-skills")
    found = skills.load()
    try:
        yield found
    finally:
        skills._skills = original


def _is_mutation(name: str, args: dict) -> bool:
    return name in MUTATING and not (name == "organize_files" and not args.get("confirm"))


@dataclass
class FakeExecutor:
    """Runs a skill's declared steps through the real policy. `answers` is what the
    person says to each card, in order; a missing answer is a no."""
    mode: str = "default"          # default | view_only
    answers: list[bool] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)     # every tool call that reached a (fake) tool
    cards: list[dict] = field(default_factory=list)     # every confirm card shown
    refused: list[str] = field(default_factory=list)

    def run(self, skill) -> "FakeExecutor":
        for step in skill.contract["steps"]:
            name, args = step["tool"], dict(step.get("args") or {})
            decision = policy.decide(REGISTRY[name].category, args, tool=name)
            if decision.tier is policy.Tier.DENY:
                self.refused.append(name)
                continue
            if decision.tier is policy.Tier.CONFIRM:
                self.cards.append({"tool": name, "args": args, "reason": decision.reason,
                                   "calls_so_far": len(self.calls)})
                if not (self.answers.pop(0) if self.answers else False):
                    self.refused.append(name)
                    continue
            self.calls.append({"tool": name, "args": args, "confirmed": decision.tier is policy.Tier.CONFIRM})
        return self

    @property
    def mutations(self) -> list[dict]:
        return [c for c in self.calls if _is_mutation(c["tool"], c["args"])]


# -- 1. each skill validates and loads ----------------------------------------

@pytest.mark.parametrize("name", WORKFLOWS)
def test_skill_loads_enabled_and_bundled(loaded, name) -> None:
    skill = loaded[name]
    assert skill.bundled and skill.enabled and not skill.error
    assert skill.description and skill.triggers
    assert 0 < len(skill.body) <= skills.MAX_BODY_CHARS
    # Ordinary trigger-matched skills: the persistent chat-only workflow path has no tools.
    assert not skill.conversation_workflow and not skill.always and not skill.tools
    assert skill.path == skills.BUNDLED_SKILLS_DIR / name


@pytest.mark.parametrize("name", WORKFLOWS)
def test_contract_validates_against_the_real_registry_and_policy(loaded, name) -> None:
    assert contracts.validate(loaded[name]) == []


def test_the_skill_files_have_no_scripts_or_shell(loaded) -> None:
    # A fixed workflow, not code: SKILL.md only, no skill-defined tools or scripts.
    for name in WORKFLOWS:
        assert [p.name for p in (skills.BUNDLED_SKILLS_DIR / name).iterdir()] == ["SKILL.md"]


# -- 2. declared tools and grants match what it calls -------------------------

@pytest.mark.parametrize("name", WORKFLOWS)
def test_steps_call_exactly_the_declared_tools(loaded, name) -> None:
    c = loaded[name].contract
    declared = set(c.get("reads", [])) | set(c.get("preview", [])) | set(c.get("confirms", []))
    assert {s["tool"] for s in c["steps"]} == declared
    assert declared <= set(REGISTRY)
    assert c["grants"] == "none"


@pytest.mark.parametrize("name", WORKFLOWS)
def test_no_workflow_calls_a_send_delete_shell_or_extension_tool(loaded, name) -> None:
    for step in loaded[name].contract["steps"]:
        assert REGISTRY[step["tool"]].category not in contracts._FORBIDDEN_CATEGORIES, step
        assert step["tool"] not in grants._NEVER_GRANTABLE, step


@pytest.mark.parametrize("name", WORKFLOWS)
def test_no_workflow_adds_or_asks_for_a_standing_grant(loaded, name) -> None:
    body = loaded[name].body.lower()
    for phrase in ("always allow", "always-allow", "allow always", "don't ask again", "remember this choice"):
        assert phrase not in body
    # The two confirming skills say outright that they never ask for one.
    for confirming in CONFIRMING_SKILLS:
        assert "standing" in loaded[confirming].body and "always" in loaded[confirming].body


def test_running_the_skills_stores_no_grant(loaded) -> None:
    for name in WORKFLOWS:
        FakeExecutor(answers=[True] * 5).run(loaded[name])
    assert grants._CACHE == {} and not grants.GRANTS_PATH.exists()


@pytest.mark.parametrize("tamper,expect", [
    (lambda c: c["confirms"].append("send_email"), "send_email"),
    (lambda c: c["steps"].append({"tool": "list_dir", "args": {"path": "~"}}), "not declared"),
    (lambda c: c.update(grants="always"), "grants: none"),
    (lambda c: c["reads"].append("no_such_tool"), "not a registered tool"),
    (lambda c: c["steps"].append({"tool": "run_shell", "args": {"cmd": "ls"}}), "run_shell"),
])
def test_validate_catches_a_tampered_contract(loaded, tamper, expect) -> None:
    skill = copy.deepcopy(loaded["inbox-cleanup"])
    tamper(skill.contract)
    assert any(expect in p for p in contracts.validate(skill)), contracts.validate(skill)


def test_validate_catches_a_file_tidy_that_leaves_the_users_folders(loaded) -> None:
    for folder, dest in (("/etc", "."), ("~", "."), ("~/Downloads", "/Applications")):
        skill = copy.deepcopy(loaded["file-tidy"])
        for step in skill.contract["steps"]:
            if step["tool"] == "organize_files":
                step["args"].update(folder=folder, destination=dest)
        assert any("outside the user's folders" in p for p in contracts.validate(skill)), (folder, dest)


def test_validate_catches_a_read_only_skill_that_gains_a_write(loaded) -> None:
    skill = copy.deepcopy(loaded["week-review"])
    skill.contract["confirms"] = ["email_triage"]
    skill.contract["steps"].append({"tool": "email_triage", "args": {"action": "archive"}, "confirm": "group"})
    assert any("read-only" in p for p in contracts.validate(skill))


# -- 3. inbox clean-up and file tidy never change anything without a confirm ----

@pytest.mark.parametrize("name", CONFIRMING_SKILLS)
def test_a_no_to_every_card_changes_nothing(loaded, name) -> None:
    ran = FakeExecutor(answers=[False] * 10).run(loaded[name])
    assert ran.cards, "the skill must reach at least one confirm card"
    assert ran.mutations == []


@pytest.mark.parametrize("name", CONFIRMING_SKILLS)
def test_every_mutation_follows_its_own_approved_card(loaded, name) -> None:
    ran = FakeExecutor(answers=[True] * 10).run(loaded[name])
    assert ran.mutations
    assert all(m["confirmed"] for m in ran.mutations), ran.calls
    assert len(ran.cards) == len(ran.mutations)


@pytest.mark.parametrize("name", CONFIRMING_SKILLS)
def test_view_only_mode_refuses_the_changes_even_if_approved(loaded, monkeypatch, name) -> None:
    monkeypatch.setattr(policy, "_READ_ONLY", True, raising=False)
    ran = FakeExecutor(answers=[True] * 10).run(loaded[name])
    assert ran.mutations == [] and ran.cards == []
    assert set(ran.refused) & {"email_triage", "organize_files"}


def test_archive_asks_in_the_default_mode_and_is_never_a_standing_allow(loaded) -> None:
    args = {"message_id": "<m1.invented@example.test>", "action": "archive"}
    assert policy.decide("email_triage", args, tool="email_triage").tier is policy.Tier.CONFIRM
    # A group's yes is for that group: the skill asks before each group and names the tool.
    body = loaded["inbox-cleanup"].body
    assert "one group at a time" in body and "`email_triage`" in body and "never batch groups" in body
    assert "delete" in body and "never deletes" in body


def test_moving_files_asks_even_under_full_access_and_ignores_a_stale_grant(loaded, monkeypatch) -> None:
    args = {"pattern": "*.pdf", "folder": "~/Downloads", "destination": ".", "group_by": "extension",
            "confirm": True, "preview_token": "t0ken"}
    grants._CACHE["organize_files"] = {"allow": [{"scope": ""}], "deny": []}
    for full in (False, True):
        monkeypatch.setattr(policy, "_FULL_ACCESS", full, raising=False)
        assert policy.decide("fs_write", args, tool="organize_files").tier is policy.Tier.CONFIRM, full
    # And without a preview token the move is refused outright.
    assert policy.decide("fs_write", {**args, "preview_token": ""}, tool="organize_files").tier is policy.Tier.DENY


def test_policy_refuses_protected_folders_and_validate_refuses_the_rest(loaded) -> None:
    for folder in ("~/.ssh", "~/Library/Keychains"):
        args = {"pattern": "*", "folder": folder, "destination": ".", "confirm": False}
        assert policy.decide("fs_write", args, tool="organize_files").tier is policy.Tier.DENY, folder
    # Folders policy would let through to a card are still outside this skill's own list.
    for folder in ("/etc", "~", "~/Applications", "/Users"):
        assert not contracts._inside_user_folders(folder), folder
    for folder in ("~/Downloads", "~/Desktop/Old stuff"):
        assert contracts._inside_user_folders(folder), folder


# -- 4. file tidy's plan is shown before any move ----------------------------

def test_file_tidy_previews_before_it_moves_and_the_move_repeats_the_previewed_inputs(loaded) -> None:
    steps = loaded["file-tidy"].contract["steps"]
    organize = [s for s in steps if s["tool"] == "organize_files"]
    assert [bool(s["args"]["confirm"]) for s in organize] == [False, True]
    preview, move = (dict(s["args"]) for s in organize)
    preview.pop("confirm"), move.pop("confirm")
    assert move.pop("preview_token") and "preview_token" not in preview
    assert preview == move, "the move must repeat exactly what was previewed"
    assert steps.index(organize[0]) < steps.index(organize[1])


def test_file_tidy_shows_a_preview_with_no_card_and_asks_before_the_move(loaded) -> None:
    ran = FakeExecutor(answers=[True]).run(loaded["file-tidy"])
    names = [(c["tool"], bool(c["args"].get("confirm"))) for c in ran.calls]
    assert names.index(("organize_files", False)) < names.index(("organize_files", True))
    assert len(ran.cards) == 1 and ran.cards[0]["args"]["confirm"] is True
    assert not any(c["confirmed"] for c in ran.calls if not c["args"].get("confirm"))
    body = loaded["file-tidy"].body
    assert "Do not move anything in the same\nreply as the plan" in body
    assert "never deletes" in body and "Downloads" in body


def test_the_real_preview_moves_nothing_and_the_real_move_needs_its_token(tmp_path, monkeypatch) -> None:
    """Invented files in a temp folder: the real organize_files, preview then (refused) move."""
    from service.tools import files_tools
    folder = tmp_path / "Downloads"
    folder.mkdir()
    for name in ("invoice-a.pdf", "invoice-b.pdf", "photo-a.png"):
        (folder / name).write_bytes(name.encode())
    before = sorted(p.name for p in folder.rglob("*"))
    preview = files_tools.organize_files("*", str(folder), ".", group_by="extension")
    assert "preview only, no filesystem changes" in preview and "Preview token" in preview
    assert sorted(p.name for p in folder.rglob("*")) == before
    refused = files_tools.organize_files("*", str(folder), ".", confirm=True, preview_token="guessed",
                                         group_by="extension")
    assert "Preview again" in refused and sorted(p.name for p in folder.rglob("*")) == before


# -- 5. meeting prep and week review are read-only -------------------------------

@pytest.mark.parametrize("name", READ_ONLY_SKILLS)
def test_read_only_skills_declare_and_do_nothing_that_changes_anything(loaded, name) -> None:
    c = loaded[name].contract
    assert c["read_only"] is True and not c.get("confirms") and not c.get("preview")
    for mode in ("default", "full", "view_only"):
        policy._FULL_ACCESS = mode == "full"
        policy._READ_ONLY = mode == "view_only"
        ran = FakeExecutor(answers=[True] * 5).run(loaded[name])
        assert ran.cards == [] and ran.mutations == [] and ran.refused == [], (name, mode)
        assert len(ran.calls) == len(c["steps"])
    assert all(REGISTRY[s["tool"]].category.endswith("_read") or REGISTRY[s["tool"]].category == "assistant_read"
               for s in c["steps"])


@pytest.mark.parametrize("name", READ_ONLY_SKILLS)
def test_read_only_skills_tell_the_model_not_to_change_anything(loaded, name) -> None:
    body = loaded[name].body.lower()
    assert "read-only" in body and "changes nothing" in body
    for tool in sorted(MUTATING):
        assert f"`{tool}`" not in loaded[name].body


# -- 6. the skills appear in the bundled listing --------------------------------

def test_bundled_listing_has_the_four_new_skills(loaded) -> None:
    bundled = {n for n, s in loaded.items() if s.bundled}
    assert set(WORKFLOWS) <= bundled
    assert bundled == {"interview-me", "idea-refine", "mac-controls", "humanizer", *WORKFLOWS}
    listed = {row["name"]: row for row in (s.as_dict() for s in loaded.values())}
    for name in WORKFLOWS:
        assert listed[name]["bundled"] and listed[name]["enabled"] and listed[name]["tools"] == []


def test_catalog_lists_them_and_triggers_load_the_right_body(loaded) -> None:
    catalog = skills.skills_context_block("what is the weather")
    for name in WORKFLOWS:
        assert f"- {name}:" in catalog and f"--- Skill: {name} ---" not in catalog
    for text, name in [("Please clean up my inbox", "inbox-cleanup"),
                       ("prep me for my next meeting", "meeting-prep"),
                       ("can you review my week", "week-review"),
                       ("tidy my Downloads folder", "file-tidy")]:
        assert skills.turn_skill_names(text) == [name], text
        assert f"--- Skill: {name} ---" in skills.skills_context_block(text)
    assert skills.turn_skill_names("what's the weather") == []


def test_use_skill_returns_the_body_and_the_toggle_keeps_it_bundled(loaded) -> None:
    import asyncio
    out = asyncio.run(REGISTRY["use_skill"].func("file-tidy"))
    assert "Move only after a yes" in out
    assert skills.set_enabled("file-tidy", False) is True
    assert skills.get("file-tidy").bundled and not skills.get("file-tidy").enabled
    assert skills.turn_skill_names("tidy my downloads") == []


def test_workflow_skills_register_no_tools(loaded) -> None:
    # No skill-defined tools, so no grants or tool-consolidation map needs an entry.
    assert all(not loaded[n].tools for n in WORKFLOWS)
    json.dumps({n: loaded[n].contract for n in WORKFLOWS})   # contracts are plain data


def test_archive_skips_its_card_under_full_access_and_the_skill_text_says_so(loaded, monkeypatch) -> None:
    args = {"message_id": "<m1.invented@example.test>", "action": "archive"}
    monkeypatch.setattr(policy, "_FULL_ACCESS", True, raising=False)
    assert policy.decide("email_triage", args, tool="email_triage").tier is policy.Tier.ALLOW
    monkeypatch.setattr(policy, "_FULL_ACCESS", False, raising=False)
    skill = loaded["inbox-cleanup"]
    assert "full access" in " ".join(skill.body.lower().split())
    assert contracts.validate(skill) == []
    # validate never borrows the person's mode.
    assert policy._FULL_ACCESS is False
    tampered = copy.deepcopy(skill)
    tampered.body = tampered.body.replace("full access", "other access")
    # As shipped, the archive comes after the skill has read mail, so it asks even
    # under Full access (policy.after_untrusted_read_decision) and needs no caveat.
    assert not any("full access" in p for p in contracts.validate(tampered))
    # An archive with no read before it would skip its card, and the text must say so.
    steps = tampered.contract["steps"]
    tampered.contract["steps"] = ([st for st in steps if st["tool"] == "email_triage"]
                                  + [st for st in steps if st["tool"] != "email_triage"])
    assert any("full access" in p for p in contracts.validate(tampered))


def test_validate_never_changes_the_access_mode_a_concurrent_turn_sees(loaded, monkeypatch) -> None:
    """The full-access check used to set policy._FULL_ACCESS for the whole process while
    it ran; a turn deciding at that moment saw Full access on. The simulated mode is
    this task's own: another thread deciding meanwhile sees the person's mode."""
    import threading
    args = {"message_id": "<m@example.test>", "action": "archive"}
    seen: dict = {}
    real_decide = policy.decide

    def other_turn() -> None:
        seen["full"] = policy.full_access()
        seen["tier"] = real_decide("email_triage", args, tool="email_triage").tier

    def decide_and_look(*a, **k):
        if "tier" not in seen:                    # while validate is inside its simulation
            worker = threading.Thread(target=other_turn)
            worker.start()
            worker.join()
        return real_decide(*a, **k)

    monkeypatch.setattr(policy, "decide", decide_and_look)
    with policy.simulated_mode(full_access=True):
        assert policy.full_access() is True
        decide_and_look("email_triage", args, tool="email_triage")
    assert seen == {"full": False, "tier": policy.Tier.CONFIRM}
    seen.clear()
    contracts.validate(loaded["file-tidy"])
    assert seen == {"full": False, "tier": policy.Tier.CONFIRM}
    assert policy._FULL_ACCESS is False and policy.full_access() is False


def test_validate_ignores_the_person_s_own_mode_and_grants(loaded, monkeypatch) -> None:
    """A contract is checked against the default mode with no standing grant, whatever
    this Mac is set to: view-only used to make every step look refused."""
    monkeypatch.setattr(policy, "_READ_ONLY", True, raising=False)
    assert contracts.validate(loaded["file-tidy"]) == []
    monkeypatch.setattr(policy, "_READ_ONLY", False, raising=False)
    monkeypatch.setattr(policy, "_FULL_ACCESS", True, raising=False)
    assert contracts.validate(loaded["week-review"]) == []
    assert policy._FULL_ACCESS is True and policy._READ_ONLY is False


@pytest.mark.parametrize("tamper,expect", [
    (lambda st: st[0].update(args={"limt": 15}), "takes no argument 'limt'"),
    (lambda st: st[0].update(args={"limit": "fifteen"}), "is not of type integer"),
    (lambda st: st[2].update(args={"mailbox": "drafts"}), "is not one of"),
    (lambda st: st[3]["args"].pop("action"), "needs action"),
    (lambda st: st.__setitem__(0, {"tool": "scan_subscriptions", "args": ["limit"]}), "args must be a mapping"),
])
def test_validate_checks_step_arguments_against_the_tool_schema(loaded, tamper, expect) -> None:
    skill = copy.deepcopy(loaded["inbox-cleanup"])
    tamper(skill.contract["steps"])
    assert any(expect in p for p in contracts.validate(skill)), contracts.validate(skill)


def test_a_confirmed_step_must_carry_every_required_argument(loaded) -> None:
    skill = copy.deepcopy(loaded["file-tidy"])
    skill.contract["steps"][2]["args"].pop("folder")
    assert any("organize_files needs folder" in p for p in contracts.validate(skill))
    # A read step's required arguments are the model's to fill (find_files' query).
    assert not any("find_files" in p for p in contracts.validate(loaded["meeting-prep"]))


def test_a_bundled_skill_whose_contract_fails_loads_as_broken(tmp_path, monkeypatch) -> None:
    import shutil
    bundled = tmp_path / "bundled"
    shutil.copytree(skills.BUNDLED_SKILLS_DIR / "week-review", bundled / "week-review")
    md = bundled / "week-review" / "SKILL.md"
    md.write_text(md.read_text().replace("{tool: get_past_events, args: {days: 7}}",
                                         "{tool: get_past_events, args: {days: seven}}"))
    original = skills.all_skills()
    monkeypatch.setattr(skills, "BUNDLED_SKILLS_DIR", bundled)
    monkeypatch.setattr(skills, "SKILLS_DIR", tmp_path / "user-skills")
    try:
        skill = skills.load()["week-review"]
        assert skill.error.startswith("its contract does not hold") and "days='seven'" in skill.error
        assert not skill.enabled and skills.turn_skill_names("review my week") == []
        assert skills.set_enabled("week-review", True) is True
        assert skills.get("week-review").enabled is False
    finally:
        monkeypatch.undo()
        skills._skills = original


def test_file_tidy_move_still_asks_under_full_access_so_its_text_needs_no_caveat(loaded) -> None:
    skill = copy.deepcopy(loaded["file-tidy"])
    skill.body = skill.body.replace("full access", "other access")
    assert contracts.validate(skill) == []


def _steps(loaded, name):
    return loaded[name].contract["steps"]


@pytest.mark.parametrize("name", ("meeting-prep", "week-review", "file-tidy"))
def test_every_step_passes_arguments_the_tool_accepts_and_all_it_requires(loaded, name):
    for step in _steps(loaded, name):
        schema = REGISTRY[step["tool"]].parameters
        args = step.get("args") or {}
        assert set(args) <= set(schema["properties"]), (step["tool"], set(args) - set(schema["properties"]))
        assert set(schema.get("required") or []) <= set(args), (step["tool"], "missing a required argument")
        for key, value in args.items():
            enum = schema["properties"][key].get("enum")
            if enum:
                assert value in enum, (step["tool"], key)


def test_meeting_prep_looks_far_enough_ahead_and_reads_notes_reminders_attachments(loaded):
    steps = _steps(loaded, "meeting-prep")
    upcoming = next(s for s in steps if s["tool"] == "get_upcoming")
    assert upcoming["args"]["days"] >= 4          # a Friday request reaches Monday
    find = next(s for s in steps if s["tool"] == "find_files")
    assert find["args"]["query"] and find["args"]["kind"]
    used = {s["tool"] for s in steps}
    assert {"search_notes", "search_reminders", "read_email_attachments"} <= used
    assert "days: 2" not in loaded["meeting-prep"].body


def test_week_review_covers_inbox_texts_notes_and_all_reminder_scopes(loaded):
    steps = _steps(loaded, "week-review")
    emails = [s["args"].get("mailbox", "inbox") for s in steps if s["tool"] == "view_emails"]
    assert sorted(emails) == ["inbox", "sent"]
    used = {s["tool"] for s in steps}
    assert {"view_messages", "search_notes"} <= used
    scopes = {s["args"]["scope"] for s in steps if s["tool"] == "search_reminders"}
    assert scopes == {"past_due", "today", "all"}


def test_file_tidy_always_leaves_out_downloads_in_progress_and_todays_files(loaded):
    moves = [s for s in _steps(loaded, "file-tidy") if s["tool"] == "organize_files"]
    assert len(moves) == 2
    for step in moves:
        args = step["args"]
        assert {".download", ".crdownload", ".part"} <= set(args["exclude_extensions"].split(","))
        assert args["min_age_hours"] >= 24
    # The confirm call repeats the preview's filters exactly, or the preview is void.
    keys = ("exclude_extensions", "min_age_hours", "group_by", "folder", "pattern", "destination")
    assert {k: moves[0]["args"][k] for k in keys} == {k: moves[1]["args"][k] for k in keys}
