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
    # The shared temp root itself (the per-user T folder, $TMPDIR's parent) is never
    # granted, for reading or writing. Under Simulation QA the skill and Harbor's folder
    # live inside it, so only their own subfolders may be allowed, nothing above them.
    shared = _shared_temp_root()
    for line in profile.splitlines():
        if line.startswith("(allow file-") and '(subpath "' in line:
            granted = Path(line.split('(subpath "', 1)[1].split('"', 1)[0])
            assert not (shared == granted or shared.is_relative_to(granted)), line
    assert '(deny file-read* (subpath "/private/var/folders"))' in profile
    assert skill not in scratch.parents and scratch != skill


def _shared_temp_root() -> Path:
    temp = Path(os.environ.get("TMPDIR") or tempfile.gettempdir()).resolve()
    for folder in (temp, *temp.parents):
        if folder.name == "T" and folder.parent.parent.parent.name == "folders":
            return folder                     # /private/var/folders/<xx>/<id>/T
    return temp.parent


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


def test_apple_events_stay_denied_by_default_and_open_only_to_named_targets(skill):
    plain = sandbox.build_profile(skill, [], [])
    assert "(deny appleevent-send)" in plain and "appleevent-destination" not in plain
    assert "com.apple.coreservices.appleevents" in plain and "com.apple.tccd" in plain

    dests = sandbox.apple_event_destinations(["System Events", "Spotify", "Terminal", "nonsense"])
    assert dests == ["com.apple.systemevents", "com.spotify.client"]   # Terminal grants nothing
    opened = sandbox.build_profile(skill, [], [], dests)
    lines = opened.splitlines()
    allow = '(allow appleevent-send (appleevent-destination "com.apple.systemevents" "com.spotify.client"))'
    assert allow in lines
    # Last matching rule wins, so the narrow allow must come after the blanket deny.
    assert lines.index(allow) > lines.index("(deny appleevent-send)")
    # Nothing else opens: Launch Services, the pasteboard and open-by-URL stay refused.
    assert "(deny lsopen)" in lines and "com.apple.coreservices.launchservicesd" in opened
    assert "com.apple.pasteboard.1" in opened and "com.apple.coreservices.appleevents" not in opened


def test_only_a_bundled_skill_can_declare_apple_events(skill, monkeypatch):
    import asyncio
    from service.skills import tools as skill_tools
    seen = []
    monkeypatch.setattr(skill_tools.sandbox, "available", lambda: True)
    monkeypatch.setattr(skill_tools.sandbox, "build_profile",
                        lambda *a, **k: seen.append(a[3] if len(a) > 3 else k.get("apple_events")) or "(version 1)\n")
    monkeypatch.setattr(skill_tools.sandbox, "wrap_argv", lambda argv, _p: ["/usr/bin/true"])
    spec = {"script": "tool.py", "apple_events": ["System Events"]}
    for bundled in (False, True):
        asyncio.run(skill_tools._make_runner(skill, spec, "fixture_tool", bundled=bundled)())
    assert seen == [[], ["com.apple.systemevents"]]


def test_mac_controls_declares_only_the_apps_it_scripts():
    spec = S.BUNDLED_SKILLS_DIR / "mac-controls" / "SKILL.md"
    tools = {t["name"]: t for t in S._parse(spec, bundled=True).tools}
    assert tools, "mac-controls tools did not parse"
    for name, tool in tools.items():
        declared = {d.lower() for d in tool["apple_events"]}
        assert declared <= {"system events", "music", "spotify"}, name
    assert {d.lower() for d in tools["mac_media"]["apple_events"]} == {"music", "spotify"}
    assert {d.lower() for d in tools["mac_lock_screen"]["apple_events"]} == {"system events"}


def test_blocked_ports_apply_to_this_mac_only_not_outside_hosts(skill, monkeypatch):
    from service.safety import shell_sandbox
    monkeypatch.setattr(shell_sandbox, "harbor_ports", lambda: [8765])
    monkeypatch.setattr(shell_sandbox, "engine_ports", lambda: [8000])
    profile = sandbox.build_profile(skill, [], [])
    assert '(remote ip "*:8765")' not in profile and '(remote ip "*:8000")' not in profile
    assert '(deny network-outbound (remote ip "localhost:8765"))' in profile
    assert '(deny network-outbound (remote ip "localhost:8000"))' in profile


def test_sandbox_probe_uses_absolute_paths_and_names_a_nested_sandbox(monkeypatch):
    class Done:
        def __init__(self, code, err=""):
            self.returncode, self.stderr = code, err
    monkeypatch.setenv("PATH", "/nonexistent")           # no /usr/bin on PATH
    cases = [(Done(0), "ready", True, ""),
             (Done(71, "sandbox_apply: Operation not permitted"), "nested", False, ""),
             (Done(1, "something else"), "broken", False, "could not be started")]
    for done, state, avail, refuse in cases:
        monkeypatch.setattr(sandbox, "_probe_cache", {})
        monkeypatch.setattr(sandbox.os.path, "isfile", lambda p: True)
        monkeypatch.setattr(sandbox.os, "access", lambda p, m: True)
        monkeypatch.setattr(sandbox.subprocess, "run", lambda *a, _d=done, **k: _d)
        assert sandbox._probe() == state
        assert sandbox.available() is avail
        assert (refuse in sandbox.refusal()) and bool(sandbox.refusal()) == bool(refuse)
    monkeypatch.setattr(sandbox, "_probe_cache", {})
    monkeypatch.setattr(sandbox.os.path, "isfile", lambda p: False)
    assert sandbox._probe() == "missing" and sandbox.refusal() == ""


@pytest.mark.skipif(not _kernel_sandbox_works(), reason="a sandbox cannot be applied inside another sandbox")
def test_the_apple_events_profile_compiles_and_runs_a_no_op(skill, tmp_path):
    profile = tmp_path / "ae.sb"
    profile.write_text(sandbox.build_profile(
        skill, [], [], sandbox.apple_event_destinations(["System Events", "Music", "Spotify"])))
    done = subprocess.run([sandbox.SANDBOX_EXEC, "-f", str(profile), "/usr/bin/true"],
                          capture_output=True, text=True, timeout=30, cwd="/")
    assert done.returncode == 0, done.stderr
