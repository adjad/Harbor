"""Candidate review #4: a SKILL.md over 1 MB is refused as broken rather than
fingerprinted on only its first megabyte, so content past that point can't change
unnoticed. Invented skills in a temp folder only.
"""
from __future__ import annotations

import os
import tempfile

import pytest

_scratch = tempfile.TemporaryDirectory(prefix="harbor-skill-size-")
os.environ.setdefault("HARBOR_HOME", _scratch.name)

import service.skills as S  # noqa: E402
from service.skills import trust  # noqa: E402


@pytest.fixture
def skill_home(monkeypatch, tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr(S, "SKILLS_DIR", skills_dir)
    monkeypatch.setattr(S, "BUNDLED_SKILLS_DIR", tmp_path / "bundled-none")
    monkeypatch.setattr(trust, "TRUST_PATH", tmp_path / "moe" / "skill_trust.json")
    yield skills_dir
    S.load()


def test_an_oversized_skill_md_loads_as_broken(skill_home):
    folder = skill_home / "fixture-big"
    folder.mkdir()
    (folder / "SKILL.md").write_text("---\nname: fixture-big\ndescription: Invented.\n---\n"
                                     + "x" * (S.MAX_SKILL_MD_BYTES + 10))
    skill = S.load()["fixture-big"]
    assert skill.error and "too large" in skill.error and not skill.enabled


def test_two_oversized_files_differing_past_a_megabyte_fingerprint_differently(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for folder, tail in ((a, "one"), (b, "two")):
        folder.mkdir()
        (folder / "SKILL.md").write_text("---\nname: f\n---\n" + "x" * (2 << 20) + tail)
    assert trust.fingerprint(a) != trust.fingerprint(b)
