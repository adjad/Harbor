"""Installing a skill never deletes its own source or the skills folder, and one bad
SKILL.md never hides the skills scanned after it. Synthetic skills only."""
from __future__ import annotations

from pathlib import Path

import pytest

from service import skills


@pytest.fixture()
def user_skills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    original = skills.all_skills()
    root = tmp_path / "user-skills"
    monkeypatch.setattr(skills, "SKILLS_DIR", root)
    monkeypatch.setattr(skills, "BUNDLED_SKILLS_DIR", tmp_path / "no-bundled")
    root.mkdir()
    try:
        yield root
    finally:
        skills._skills = original


def _skill(folder: Path, name: str, extra: str = "") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: synthetic\n{extra}---\nDo the thing.\n")
    return folder


def test_reinstalling_the_installed_copy_keeps_it(user_skills: Path) -> None:
    installed = _skill(user_skills / "demo-skill", "demo-skill")
    result = skills.install(str(installed))
    assert result["ok"], result
    assert (installed / "SKILL.md").exists()
    assert "demo-skill" in skills.all_skills()


def test_installing_the_skills_folder_or_its_parent_is_refused(user_skills: Path) -> None:
    _skill(user_skills / "keep-me", "keep-me")
    (user_skills / "SKILL.md").write_text("---\nname: odd\n---\nbody\n")
    for target in (user_skills, user_skills.parent):
        (target / "SKILL.md").write_text("---\nname: odd\n---\nbody\n")
        result = skills.install(str(target))
        assert not result["ok"]
        assert (user_skills / "keep-me" / "SKILL.md").exists()


def test_update_replaces_the_old_copy(user_skills: Path, tmp_path: Path) -> None:
    source = _skill(tmp_path / "src" / "demo-skill", "demo-skill")
    assert skills.install(str(source))["ok"]
    (source / "SKILL.md").write_text("---\nname: demo-skill\ndescription: v2\n---\nNew body.\n")
    assert skills.install(str(source))["ok"]
    assert "New body." in (user_skills / "demo-skill" / "SKILL.md").read_text()
    assert not [p for p in user_skills.iterdir() if p.name.startswith(".")]


def test_malformed_triggers_do_not_hide_later_skills(user_skills: Path) -> None:
    _skill(user_skills / "a-bad", "a-bad", "triggers: 5\n")
    _skill(user_skills / "b-none", "b-none", "triggers: [~, '', ' plan ']\n")
    _skill(user_skills / "c-good", "c-good", "triggers: [weekly]\n")
    loaded = skills.load()
    assert {"a-bad", "b-none", "c-good"} <= set(loaded)
    assert loaded["a-bad"].triggers == []
    assert loaded["b-none"].triggers == ["plan"]
    assert loaded["c-good"].triggers == ["weekly"]
