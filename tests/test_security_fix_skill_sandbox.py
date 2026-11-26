"""#2 and #3: a skill tool may not rewrite its own SKILL.md or script, may not hand work
to a process outside its sandbox (open, osascript, new launchd jobs), and writes only
to its own scratch folder. The profile text is checked everywhere; where the kernel
sandbox can run (not inside another sandbox), it is checked for real with harmless
probes. Invented skills in a temp folder only.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

_scratch = tempfile.TemporaryDirectory(prefix="harbor-skill-sbx-")
os.environ.setdefault("HARBOR_HOME", _scratch.name)

import service.skills as S  # noqa: E402
from service.skills import sandbox  # noqa: E402


@pytest.fixture
def skill(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "SKILLS_DIR", tmp_path / "skills")
    folder = tmp_path / "skills" / "fixture"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("---\nname: fixture\n---\nInvented.\n")
    (folder / "tool.py").write_text("print('fixture')\n")
    return folder


def test_the_skill_folder_is_never_writable_even_with_home_granted(skill):
    profile = sandbox.build_profile(skill, [], [Path.home()])
    lines = profile.splitlines()
    deny = f'(deny file-write* (subpath "{skill.resolve()}"))'
    assert deny in lines
    # The deny comes after every allow, so a granted scope can't reopen it.
    assert lines.index(deny) > max(i for i, line in enumerate(lines) if line.startswith("(allow file-write*"))
    assert f'(deny file-write* (subpath "{S.SKILLS_DIR}"))' in lines
    from service.skills.trust import TRUST_PATH
    assert f'(deny file-write* (subpath "{TRUST_PATH}"))' in lines


def test_scratch_is_its_own_folder_and_the_shared_temp_is_gone(skill):
    profile = sandbox.build_profile(skill, [], [])
    scratch = sandbox.scratch_dir(skill)
    assert f'(allow file-write* (subpath "{scratch}"))' in profile
    assert "/private/var/folders" not in profile
    assert skill not in scratch.parents and scratch != skill


def test_no_launch_services_apple_events_or_new_jobs(skill):
    profile = sandbox.build_profile(skill, [], [])
    for rule in ("(deny appleevent-send)", "(deny lsopen)", "(deny job-creation)",
                 'global-name "com.apple.coreservices.launchservicesd"',
                 'global-name "com.apple.coreservices.appleevents"', 'global-name "com.apple.tccd"'):
        assert rule in profile, rule
    assert profile.rstrip().splitlines()[-1].startswith("(deny network-outbound")


def _kernel_sandbox_works() -> bool:
    if not sandbox.available():
        return False
    try:
        return subprocess.run([sandbox.SANDBOX_EXEC, "-p", "(version 1)(allow default)", "/usr/bin/true"],
                              capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@pytest.mark.skipif(not _kernel_sandbox_works(), reason="a sandbox cannot be applied inside another sandbox")
def test_the_kernel_refuses_a_rewrite_of_its_own_skill_md(skill, tmp_path):
    profile = tmp_path / "p.sb"
    profile.write_text(sandbox.build_profile(skill, [], []))
    scratch = sandbox.scratch_dir(skill)
    scratch.mkdir(parents=True, exist_ok=True)
    run = lambda script: subprocess.run([sandbox.SANDBOX_EXEC, "-f", str(profile), "/bin/sh", "-c", script],
                                        capture_output=True, text=True, timeout=30, cwd="/")
    assert run(f"echo 'write_scopes: [~]' >> '{skill}/SKILL.md'").returncode != 0
    assert run(f"echo x > '{skill}/tool.py'").returncode != 0
    assert (skill / "SKILL.md").read_text().endswith("Invented.\n")
    assert run(f"echo ok > '{scratch}/note' && cat '{scratch}/note'").stdout.strip() == "ok"
    # An Apple Event to a harmless target is refused.
    events = subprocess.run([sandbox.SANDBOX_EXEC, "-f", str(profile), "/usr/bin/osascript", "-e",
                             'tell application "System Events" to count processes'],
                            capture_output=True, text=True, timeout=30)
    assert events.returncode != 0



def test_scratch_is_allowed_under_both_spellings_of_a_linked_home(tmp_path, monkeypatch):
    real = tmp_path / "real-home"
    (real / "skill-scratch").mkdir(parents=True)
    linked = tmp_path / "linked-home"
    linked.symlink_to(real)
    import service.paths as P
    monkeypatch.setattr(P, "MOE_DIR", linked)
    folder = tmp_path / "skills" / "fixture"
    folder.mkdir(parents=True)
    profile = sandbox.build_profile(folder, [], [])
    for base in (linked, real.resolve()):
        assert f'(allow file-write* (subpath "{base / "skill-scratch" / "fixture"}"))' in profile
