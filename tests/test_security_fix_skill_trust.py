"""#1 and #2: a skill folder Harbor didn't install loads switched off, and a skill that
changes after approval (a tool rewriting its own SKILL.md scopes) is off again until
the person switches it back on. Invented skills in a temp folder only.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_scratch = tempfile.TemporaryDirectory(prefix="harbor-skill-trust-")
os.environ.setdefault("HARBOR_HOME", _scratch.name)

import service.skills as S  # noqa: E402
from service.skills import trust  # noqa: E402
from service.tools.registry import REGISTRY  # noqa: E402

SKILL = """---
name: fixture-notes
description: Tidies invented notes.
enabled: true
tools:
  - name: fixture_tidy
    description: Tidy the notes folder.
    script: tool.py
    write_scopes: []
---

Tidy the notes.
"""


@pytest.fixture(autouse=True)
def skill_home(monkeypatch, tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr(S, "SKILLS_DIR", skills_dir)
    monkeypatch.setattr(S, "BUNDLED_SKILLS_DIR", tmp_path / "bundled-none")
    monkeypatch.setattr(trust, "TRUST_PATH", tmp_path / "moe" / "skill_trust.json")
    yield skills_dir
    S.load()


def _folder(root: Path, text: str = SKILL) -> Path:
    folder = root / "fixture-notes"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(text)
    (folder / "tool.py").write_text("print('tidied')\n")
    return folder


def test_a_folder_dropped_into_the_skills_folder_loads_switched_off(skill_home):
    _folder(skill_home)
    skill = S.load()["fixture-notes"]
    assert skill.enabled is False and skill.trust_note == trust.NOT_APPROVED
    assert "fixture_tidy" not in REGISTRY


def test_a_skill_installed_from_settings_is_on(skill_home, tmp_path):
    source = _folder(tmp_path / "source")
    result = S.install(str(source))
    assert result["ok"]
    skill = S.all_skills()["fixture-notes"]
    assert skill.enabled is True and skill.trust_note == ""
    assert "fixture_tidy" in REGISTRY


def test_widening_its_own_scopes_after_approval_switches_it_off(skill_home, tmp_path):
    S.install(str(_folder(tmp_path / "source")))
    md = skill_home / "fixture-notes" / "SKILL.md"
    md.write_text(md.read_text().replace("write_scopes: []", "write_scopes: ['~/Documents']"))
    skill = S.load()["fixture-notes"]
    assert skill.enabled is False and skill.trust_note == trust.CHANGED
    assert "fixture_tidy" not in REGISTRY
    # The person switches it back on in Settings: approved as it is now.
    assert S.set_enabled("fixture-notes", True)
    assert S.all_skills()["fixture-notes"].enabled is True and "fixture_tidy" in REGISTRY


def test_a_changed_script_switches_it_off_too(skill_home, tmp_path):
    S.install(str(_folder(tmp_path / "source")))
    (skill_home / "fixture-notes" / "tool.py").write_text("import os\nos.system('true')\n")
    assert S.load()["fixture-notes"].enabled is False


def test_switching_off_and_on_in_settings_is_not_a_change(skill_home, tmp_path):
    S.install(str(_folder(tmp_path / "source")))
    assert S.set_enabled("fixture-notes", False)
    assert S.all_skills()["fixture-notes"].trust_note == ""
    assert S.set_enabled("fixture-notes", True)
    assert S.all_skills()["fixture-notes"].enabled is True


def test_an_unapproved_copy_of_a_bundled_skill_shows_switched_off(skill_home, tmp_path, monkeypatch):
    bundled = tmp_path / "bundled"
    _folder(bundled, SKILL.replace("Tidies invented notes.", "The shipped one."))
    monkeypatch.setattr(S, "BUNDLED_SKILLS_DIR", bundled)
    _folder(skill_home, SKILL.replace("Tidies invented notes.", "A copy nobody approved."))
    skill = S.load()["fixture-notes"]
    assert not skill.bundled and skill.enabled is False and skill.trust_note == trust.NOT_APPROVED
    assert "fixture_tidy" not in REGISTRY


def test_uninstall_forgets_the_approval(skill_home, tmp_path):
    S.install(str(_folder(tmp_path / "source")))
    assert S.uninstall("fixture-notes")["ok"]
    _folder(skill_home)                       # the same name written back later
    assert S.load()["fixture-notes"].enabled is False


def test_the_trust_record_is_private_and_outside_every_skill_folder(skill_home, tmp_path):
    S.install(str(_folder(tmp_path / "source")))
    assert oct(trust.TRUST_PATH.stat().st_mode & 0o777) == "0o600"
    assert skill_home not in trust.TRUST_PATH.parents


@pytest.mark.parametrize("planted", [".helper.sh", "__pycache__/tool.cpython-314.pyc"])
def test_a_planted_dotfile_or_bytecode_switches_it_off(skill_home, tmp_path, planted):
    S.install(str(_folder(tmp_path / "source")))
    target = skill_home / "fixture-notes" / planted
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"fixture payload")
    assert S.load()["fixture-notes"].enabled is False


def test_content_behind_a_link_changing_switches_it_off(skill_home, tmp_path):
    outside = tmp_path / "outside.py"
    outside.write_text("print('fixture one')\n")
    folder = _folder(skill_home)
    (folder / "linked.py").symlink_to(outside)
    trust.approve(folder)                        # approved with the link in it
    assert trust.problem(folder) == ""
    outside.write_text("print('fixture two')\n")
    assert trust.problem(folder) == trust.CHANGED


def test_finders_ds_store_is_not_a_change(skill_home, tmp_path):
    S.install(str(_folder(tmp_path / "source")))
    (skill_home / "fixture-notes" / ".DS_Store").write_bytes(b"\0fixture")
    assert S.load()["fixture-notes"].enabled is True


def test_content_behind_a_folder_link_changing_switches_it_off(skill_home, tmp_path):
    outside = tmp_path / "outside-lib"
    outside.mkdir()
    (outside / "run.sh").write_text("echo fixture one\n")
    folder = _folder(skill_home)
    (folder / "lib").symlink_to(outside)
    trust.approve(folder)
    assert trust.problem(folder) == ""
    (outside / "run.sh").write_text("echo fixture two\n")
    assert trust.problem(folder) == trust.CHANGED
    trust.approve(folder)
    (outside / "new.sh").write_text("echo fixture new\n")
    assert trust.problem(folder) == trust.CHANGED


def test_a_pipe_behind_a_folder_link_cannot_block_loading(skill_home, tmp_path):
    import threading
    outside = tmp_path / "outside-pipe"
    outside.mkdir()
    os.mkfifo(outside / "fixture.fifo")
    folder = _folder(skill_home)
    (folder / "lib").symlink_to(outside)
    done = []
    worker = threading.Thread(target=lambda: done.append(trust.fingerprint(folder)), daemon=True)
    worker.start()
    worker.join(5)
    assert done, "fingerprint blocked on a FIFO"


def test_a_too_large_linked_tree_never_matches_an_approval(skill_home, tmp_path, monkeypatch):
    monkeypatch.setattr(trust, "_LINKED_TREE_FILES", 3)
    outside = tmp_path / "outside-big"
    outside.mkdir()
    for i in range(5):
        (outside / f"f{i}.sh").write_text("echo fixture\n")
    folder = _folder(skill_home)
    (folder / "lib").symlink_to(outside)
    trust.approve(folder)
    assert trust.problem(folder) == trust.CHANGED


def test_retargeting_a_folder_link_inside_a_linked_tree_counts(skill_home, tmp_path):
    outside = tmp_path / "outside-nest"
    (outside).mkdir()
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (outside / "inner").symlink_to(tmp_path / "a")
    folder = _folder(skill_home)
    (folder / "lib").symlink_to(outside)
    trust.approve(folder)
    (outside / "inner").unlink()
    (outside / "inner").symlink_to(tmp_path / "b")
    assert trust.problem(folder) == trust.CHANGED


# -- tools audit 5.4: re-checked at use, safe SKILL.md reads, parsing, install ----

def test_a_skill_changed_after_load_is_refused_at_use_and_switched_off(skill_home, tmp_path):
    S.install(str(_folder(tmp_path / "source", SKILL.replace("Tidy the notes.",
                                                              "Tidy the notes. fixture body"))))
    S.all_skills()["fixture-notes"].triggers = ["tidy the fixture"]
    assert S.verify_before_run("fixture-notes") == ""
    assert "fixture body" in S.skills_context_block("tidy the fixture")
    (skill_home / "fixture-notes" / "tool.py").write_text("print('swapped')\n")   # no reload
    why = S.verify_before_run(skill_home / "fixture-notes")
    assert trust.CHANGED in why
    assert S.all_skills()["fixture-notes"].enabled is False
    assert "fixture_tidy" not in REGISTRY
    assert "fixture body" not in S.skills_context_block("tidy the fixture")
    assert S.verify_before_run("no-such-skill")


def test_injection_re_checks_the_approval_too(skill_home, tmp_path):
    S.install(str(_folder(tmp_path / "source", SKILL.replace("enabled: true", "enabled: true\nalways: true"))))
    assert "Tidy the notes." in S.always_skills_block()
    (skill_home / "fixture-notes" / "tool.py").write_text("print('swapped')\n")
    assert S.always_skills_block() == ""


def test_approve_fingerprints_under_its_lock(skill_home, monkeypatch):
    folder = _folder(skill_home)
    held = []
    real = trust.fingerprint

    def spy(path):
        held.append(trust._LOCK.locked())
        return real(path)
    monkeypatch.setattr(trust, "fingerprint", spy)
    trust.approve(folder)
    assert held == [True]


def test_mixed_yaml_keys_still_leave_the_on_off_flag_out_of_the_fingerprint(skill_home):
    text = SKILL.replace("enabled: true\n", "enabled: true\n1: one\nletters: two\n")
    folder = _folder(skill_home, text)
    before = trust.fingerprint(folder)
    (folder / "SKILL.md").write_text(text.replace("enabled: true", "enabled: false"))
    assert trust.fingerprint(folder) == before
    # An all-string frontmatter keeps the fingerprint it always had.
    assert trust._canonical({"b": {"a": [1]}}) == {"b": {"a": [1]}}


def test_a_linked_skill_md_is_refused_unread(skill_home, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text(SKILL)
    folder = skill_home / "fixture-link"
    folder.mkdir()
    (folder / "SKILL.md").symlink_to(outside)
    skill = S.load()["fixture-link"]
    assert "link" in skill.error and not skill.enabled


def test_a_fifo_skill_md_cannot_block_loading(skill_home):
    import threading
    folder = skill_home / "fixture-fifo"
    folder.mkdir()
    os.mkfifo(folder / "SKILL.md")
    done = []
    worker = threading.Thread(target=lambda: done.append(S.load()), daemon=True)
    worker.start()
    worker.join(5)
    assert done, "loading blocked on a FIFO SKILL.md"
    assert "not a regular file" in done[0]["fixture-fifo"].error


@pytest.mark.parametrize("text,name,body", [
    ("﻿---\nname: fixture-bom\n---\n\nBody after a BOM.\n", "fixture-bom", "Body after a BOM."),
    ("---\n---\n\nEmpty frontmatter body.\n", "fixture-x", "Empty frontmatter body."),
    ("---\nname: fixture-rule\n---\n\nPart one.\n\n---\n\nPart two.\n", "fixture-rule",
     "Part one.\n\n---\n\nPart two."),
    ("---\r\nname: fixture-crlf\r\n---  \r\nThe body.\r\n", "fixture-crlf", "The body."),
])
def test_frontmatter_edge_cases(skill_home, text, name, body):
    folder = skill_home / "fixture-x"
    folder.mkdir()
    (folder / "SKILL.md").write_text(text)
    skill = S._parse(folder / "SKILL.md")
    assert not skill.error, skill.error
    assert skill.name == name and skill.body == body


def test_set_enabled_works_without_frontmatter_and_writes_atomically(skill_home, monkeypatch):
    folder = skill_home / "fixture-plain"
    folder.mkdir()
    (folder / "SKILL.md").write_text("Just instructions, no frontmatter.\n")
    trust.approve(folder)
    S.load()
    written = []
    real_replace = os.replace
    monkeypatch.setattr(S.os, "replace", lambda a, b: (written.append((a, b)), real_replace(a, b)))
    assert S.set_enabled("fixture-plain", False) is True
    assert written and Path(written[0][1]) == folder / "SKILL.md"
    skill = S.get("fixture-plain")
    assert skill.enabled is False and skill.body == "Just instructions, no frontmatter."
    assert S.set_enabled("fixture-plain", True) is True and S.get("fixture-plain").enabled is True
    assert not list(folder.glob(".*.tmp"))


def test_parse_keeps_explicit_empty_lists_and_reads_quoted_booleans(skill_home):
    folder = skill_home / "fixture-flags"
    folder.mkdir()
    (folder / "SKILL.md").write_text(
        "---\nname: fixture-flags\ntriggers: []\ntools: []\nenabled: 'false'\nalways: 'no'\n"
        "conversation_workflow: 'true'\ncontract: {}\n"
        "metadata:\n  harbor:\n    triggers: [should not apply]\n"
        "    tools: [{name: fixture_hidden, command: echo}]\n---\n\nBody.\n")
    skill = S._parse(folder / "SKILL.md")
    assert skill.triggers == [] and skill.tools == []
    assert skill.enabled is False and skill.always is False and skill.conversation_workflow is True


def test_workflow_fields_are_read_at_the_top_level_too(skill_home):
    folder = skill_home / "fixture-top"
    folder.mkdir()
    (folder / "SKILL.md").write_text("---\nname: fixture-top\nconversation_workflow: true\n"
                                     "contract: {read_only: true}\n---\n\nBody.\n")
    skill = S._parse(folder / "SKILL.md")
    assert skill.conversation_workflow is True and skill.contract == {"read_only": True}


def test_an_overlong_body_is_reported_not_cut(skill_home):
    folder = _folder(skill_home, SKILL.replace("Tidy the notes.", "y" * (S.MAX_BODY_CHARS + 1)))
    trust.approve(folder)
    skill = S.load()["fixture-notes"]
    assert "limit" in skill.error and not skill.enabled and len(skill.body) == S.MAX_BODY_CHARS + 1


def test_a_user_skill_cannot_take_a_bundled_skill_s_name(skill_home, tmp_path, monkeypatch):
    bundled = tmp_path / "bundled"
    _folder(bundled, SKILL.replace("Tidies invented notes.", "The shipped one."))
    monkeypatch.setattr(S, "BUNDLED_SKILLS_DIR", bundled)
    other = skill_home / "fixture-other"
    other.mkdir()
    (other / "SKILL.md").write_text(SKILL.replace("Tidies invented notes.", "An impostor."))
    trust.approve(other)
    found = S.load()
    assert found["fixture-notes"].bundled and found["fixture-notes"].description == "The shipped one."
    assert "ships with Harbor" in found["fixture-other"].error and not found["fixture-other"].enabled


def test_installing_a_md_over_an_old_skill_drops_its_scripts(skill_home, tmp_path):
    S.install(str(_folder(tmp_path / "source")))
    assert (skill_home / "fixture-notes" / "tool.py").exists()
    single = tmp_path / "fixture-notes.md"
    single.write_text("---\nname: fixture-notes\n---\n\nInstructions only.\n")
    assert S.install(str(single))["ok"]
    assert sorted(p.name for p in (skill_home / "fixture-notes").iterdir()) == ["SKILL.md"]
    assert S.get("fixture-notes").enabled is True


def test_a_source_folder_holding_a_link_is_refused(skill_home, tmp_path):
    source = _folder(tmp_path / "source")
    (source / "secret.txt").symlink_to(tmp_path / "elsewhere.txt")
    (tmp_path / "elsewhere.txt").write_text("fixture")
    result = S.install(str(source))
    assert not result["ok"] and "link" in result["error"]
    assert not (skill_home / "fixture-notes").exists()


# -- tools audit 5.7: explicit mentions and stop phrases --------------------------

@pytest.mark.parametrize("text,hit", [
    ("@fixture-notes please", True), ("use $fixture-notes now", True), ("(@fixture-notes)", True),
    ("mail user@fixture-notes.com", False), ("@fixture-notes-v2 instead", False),
    ("x$fixture-notes", False), ("ping @fixture-notes.", True),
])
def test_explicit_mentions_are_token_bounded(text, hit):
    assert S.explicit_mention("fixture-notes", text) is hit


@pytest.mark.parametrize("text,stop", [
    ("stop the interview", True), ("Stop interviewing me", True), ("let's end this workflow", True),
    ("don't stop the interview", False), ("please do not stop the interview", False),
    ("never end the brainstorm", False), ("I don't want to stop the interview yet", False),
])
def test_a_negated_stop_phrase_is_not_a_stop(text, stop):
    assert S.asks_to_stop_workflow(text) is stop


def test_only_a_bare_fence_line_closes_the_frontmatter():
    assert trust.split_frontmatter("---\na: 1\n----\n--- note\n") is None
    assert trust.split_frontmatter("---\na: 1\n---\nbody\n") == ("a: 1\n", "body\n")
    assert trust.split_frontmatter("no fence\n---\n") is None
