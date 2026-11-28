"""Candidate review #1: a skill's declared scope may not name Harbor's own folder or a
place whose files run as code later (~/Library, LaunchAgents, shell rc files), and
the sandbox denies writes there even if such a scope got through. Invented paths
and a temp HARBOR_HOME only.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

_scratch = tempfile.TemporaryDirectory(prefix="harbor-skill-scopes-")
os.environ.setdefault("HARBOR_HOME", _scratch.name)

import service.skills as S  # noqa: E402
from service.paths import MOE_DIR  # noqa: E402
from service.skills import sandbox, scopes  # noqa: E402


@pytest.mark.parametrize("raw", ["~/Library", "~/Library/LaunchAgents", "~/.zshrc",
                                 "~/.config/git", str(MOE_DIR), str(MOE_DIR / "skills"),
                                 str(MOE_DIR / "fixture-sub")])
def test_code_run_places_and_harbors_folder_are_refused_as_scopes(raw):
    paths, error = scopes.parse_scopes([raw])
    assert error and not paths, (raw, paths)


def test_an_ordinary_folder_is_still_a_scope():
    paths, error = scopes.parse_scopes(["~/Documents/fixture-notes"])
    assert not error and paths


def test_the_profile_denies_them_even_with_a_scope_that_covers_them(tmp_path):
    folder = tmp_path / "skills" / "fixture"
    folder.mkdir(parents=True)
    agents = Path.home() / "Library" / "LaunchAgents"
    lines = sandbox.build_profile(folder, [], [Path.home(), MOE_DIR]).splitlines()
    last_allow_home = max(i for i, l in enumerate(lines) if l == f'(allow file-write* (subpath "{Path.home()}"))')
    for target in (MOE_DIR, agents, Path.home() / ".zshrc"):
        deny = f'(deny file-write* (subpath "{target}"))'
        assert deny in lines and lines.index(deny) > last_allow_home, target
    scratch = sandbox.scratch_dir(folder)
    allow = f'(allow file-write* (subpath "{scratch}"))'
    assert max(i for i, l in enumerate(lines) if l == allow) > lines.index(f'(deny file-write* (subpath "{MOE_DIR}"))')


def _kernel_sandbox_works() -> bool:
    if not sandbox.available():
        return False
    try:
        return subprocess.run([sandbox.SANDBOX_EXEC, "-p", "(version 1)(allow default)", "/usr/bin/true"],
                              capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@pytest.mark.skipif(not _kernel_sandbox_works(), reason="a sandbox cannot be applied inside another sandbox")
def test_the_kernel_refuses_a_write_into_harbors_folder_under_a_wide_scope(tmp_path):
    folder = tmp_path / "skills" / "fixture"
    folder.mkdir(parents=True)
    profile = tmp_path / "p.sb"
    profile.write_text(sandbox.build_profile(folder, [], [MOE_DIR]))
    scratch = sandbox.scratch_dir(folder)
    scratch.mkdir(parents=True, exist_ok=True)
    MOE_DIR.mkdir(parents=True, exist_ok=True)
    planted = MOE_DIR / "fixture-planted.txt"
    run = lambda script: subprocess.run([sandbox.SANDBOX_EXEC, "-f", str(profile), "/bin/sh", "-c", script],
                                        capture_output=True, text=True, timeout=30, cwd="/")
    assert run(f"echo x > '{planted}'").returncode != 0 and not planted.exists()
    assert run(f"echo ok > '{scratch}/note' && cat '{scratch}/note'").stdout.strip() == "ok"


def test_every_deny_is_written_under_both_spellings_of_a_linked_path(tmp_path, monkeypatch):
    real = tmp_path / "real-moe"
    real.mkdir()
    linked = tmp_path / "linked-moe"
    linked.symlink_to(real)
    import service.paths as P
    monkeypatch.setattr(P, "MOE_DIR", linked)
    folder = tmp_path / "skills" / "fixture"
    folder.mkdir(parents=True)
    profile = sandbox.build_profile(folder, [], [])
    for base in (linked, real.resolve()):
        assert f'(deny file-read* (subpath "{base / "grants.json"}"))' in profile
        assert f'(deny file-write* (subpath "{base}"))' in profile


def test_the_shared_temp_folders_are_not_readable(tmp_path):
    folder = tmp_path / "skills" / "fixture"
    folder.mkdir(parents=True)
    profile = sandbox.build_profile(folder, [], [])
    assert '(deny file-read* (subpath "/private/var/folders"))' in profile


@pytest.mark.skipif(not _kernel_sandbox_works(), reason="a sandbox cannot be applied inside another sandbox")
def test_python_still_starts_and_cannot_read_another_apps_temp(tmp_path):
    python = "/usr/bin/python3"           # a venv under /Users is not readable in the box
    if not Path(python).exists():
        pytest.skip("no system python3")
    folder = tmp_path / "skills" / "fixture"
    folder.mkdir(parents=True)
    profile = tmp_path / "p.sb"
    profile.write_text(sandbox.build_profile(folder, [], []))
    user_temp = subprocess.run(["/usr/bin/getconf", "DARWIN_USER_TEMP_DIR"], capture_output=True,
                               text=True, timeout=10).stdout.strip() or "/private/var/folders"
    probe = f"import os; print(os.listdir({str(Path(user_temp).resolve())!r}))"
    scratch = sandbox.scratch_dir(folder)
    scratch.mkdir(parents=True, exist_ok=True)
    env = {"PATH": "/usr/bin:/bin", "TMPDIR": str(scratch) + "/"}
    ok = subprocess.run([sandbox.SANDBOX_EXEC, "-f", str(profile), python, "-c", "print('fixture')"],
                        capture_output=True, text=True, timeout=60, env=env, cwd="/")
    assert ok.returncode == 0 and ok.stdout.strip() == "fixture", ok.stderr
    read = subprocess.run([sandbox.SANDBOX_EXEC, "-f", str(profile), python, "-c", probe],
                          capture_output=True, text=True, timeout=60, env=env, cwd="/")
    assert read.returncode != 0


# -- tools audit 5.2: symlinked home, shared temp and volumes, credential folders --

def test_a_linked_home_still_refuses_the_whole_home_folder(tmp_path, monkeypatch):
    real = tmp_path / "real-home"
    (real / "Documents").mkdir(parents=True)
    link = tmp_path / "home-link"
    link.symlink_to(real)
    monkeypatch.setenv("HOME", str(link))
    for raw in ("~", str(link), str(real), "~/Documents/.."):
        paths, error = scopes.parse_scopes([raw])
        assert error and not paths, raw
    paths, error = scopes.parse_scopes(["~/Documents"])
    assert not error and paths == [real / "Documents"]
    assert "~/Documents" in scopes.describe(paths, [])


@pytest.mark.parametrize("raw", ["/tmp", "/private/tmp", "/var/tmp", "/private/var/tmp", "/Volumes"])
def test_shared_temp_folders_and_volumes_are_refused(raw):
    paths, error = scopes.parse_scopes([raw])
    assert error and not paths, raw


def test_a_folder_on_an_external_volume_is_still_a_scope():
    paths, error = scopes.parse_scopes(["/Volumes/FixtureDrive/Photos"])
    assert not error and paths


@pytest.mark.parametrize("raw", ["~/.ssh", "~/.ssh/fixture_key", "~/.aws", "~/.gnupg", "~/.netrc",
                                 "~/.config/gh", "~/.config/gcloud/fixture", "~/.config",
                                 "~/Library/Keychains"])
def test_credential_folders_are_refused_when_parsing(raw):
    paths, error = scopes.parse_scopes([raw])
    assert error and not paths, raw


def test_a_linked_home_refuses_its_credential_folders_too(tmp_path, monkeypatch):
    real = tmp_path / "real-home"
    (real / ".ssh").mkdir(parents=True)
    link = tmp_path / "home-link"
    link.symlink_to(real)
    monkeypatch.setenv("HOME", str(link))
    paths, error = scopes.parse_scopes(["~/.ssh"])
    assert error and "keys or credentials" in error and not paths


def test_a_bad_scope_makes_the_skill_load_as_broken(tmp_path, monkeypatch):
    from service.skills import trust
    skills_dir = tmp_path / "skills"
    folder = skills_dir / "fixture-scope"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        "---\nname: fixture-scope\ndescription: Invented.\ntools:\n  - name: fixture_scope_tool\n"
        "    command: echo hi\n    read_scopes: ['~/.ssh']\n---\n\nInvented.\n")
    monkeypatch.setattr(S, "SKILLS_DIR", skills_dir)
    monkeypatch.setattr(S, "BUNDLED_SKILLS_DIR", tmp_path / "bundled-none")
    monkeypatch.setattr(trust, "TRUST_PATH", tmp_path / "moe" / "skill_trust.json")
    trust.approve(folder)
    try:
        skill = S.load()["fixture-scope"]
        assert "read_scopes" in skill.error and not skill.enabled
        from service.tools.registry import REGISTRY
        assert "fixture_scope_tool" not in REGISTRY
    finally:
        monkeypatch.undo()
        S.load()
