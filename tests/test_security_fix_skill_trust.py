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
