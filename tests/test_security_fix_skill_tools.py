"""#8: a skill tool stuck in a printing loop is stopped once its output passes the
ceiling, instead of filling the service's memory until the timeout.
#23: a second skill cannot take over a tool name another skill already provides.
Invented skills and commands only; the kernel sandbox is switched off here.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

_scratch = tempfile.TemporaryDirectory(prefix="harbor-skill-tools-")
os.environ.setdefault("HARBOR_HOME", _scratch.name)

import service.skills as S  # noqa: E402
from service.skills import sandbox, tools as T  # noqa: E402
from service.tools.registry import REGISTRY  # noqa: E402


@pytest.fixture(autouse=True)
def no_kernel_sandbox(monkeypatch):
    monkeypatch.setattr(sandbox, "available", lambda: False)
    monkeypatch.setattr(T, "_TIMEOUT_S", 3)


def test_a_printing_loop_is_stopped_at_the_ceiling(tmp_path):
    loop = f"{sys.executable} -c \"import sys\nwhile True: sys.stdout.write('x' * 4096)\""
    runner = T._make_runner(tmp_path, {"command": loop}, "fixture_loop")
    started = time.monotonic()
    result = str(asyncio.run(runner()))
    assert time.monotonic() - started < 2.5          # well before the timeout
    assert "truncated" in result and "was stopped" in result
    assert len(result) <= T._MAX_OUTPUT + 200


def test_ordinary_output_is_unchanged(tmp_path):
    runner = T._make_runner(tmp_path, {"command": f"{sys.executable} -c \"print('fixture done')\""},
                            "fixture_ok")
    assert str(asyncio.run(runner())) == "fixture done"


def _skill(name: str, description: str):
    return S.Skill(name=name, description=description, body="", path=Path("/nonexistent") / name,
                   tools=[{"name": "fixture_shared", "description": description, "command": "true"}])


def test_a_second_skill_cannot_take_over_a_tool_name():
    first, second = _skill("fixture-first", "the first one"), _skill("fixture-second", "a later one")
    try:
        added = T.register_skill_tools({"fixture-first": first, "fixture-second": second})
        assert added == ["fixture_shared"]
        assert "the first one" in REGISTRY["fixture_shared"].description
        # The same holds on a reload in the other order: whichever comes first keeps it.
        T.register_skill_tools({"fixture-second": second, "fixture-first": first})
        assert "a later one" in REGISTRY["fixture_shared"].description
    finally:
        T.register_skill_tools({})
    assert "fixture_shared" not in REGISTRY


ESCAPER = r'''
import os, sys
pid = os.fork()
if pid == 0:
    os.setsid()                      # leaves the tool's process group
    open(sys.argv[1], "w").write(str(os.getpid()))
    while True:
        sys.stdout.write("x" * 4096)
else:
    while True:
        sys.stdout.write("y" * 4096)
'''


def test_a_grandchild_that_leaves_the_group_cannot_hold_the_service(tmp_path, monkeypatch):
    import shlex
    import signal
    script = tmp_path / "escaper.py"
    script.write_text(ESCAPER)
    pid_file = tmp_path / "grandchild.pid"
    monkeypatch.setattr(T, "_TIMEOUT_S", 30)
    runner = T._make_runner(tmp_path, {"command": f"{shlex.quote(sys.executable)} {shlex.quote(str(script))} "
                                                  f"{shlex.quote(str(pid_file))}"}, "fixture_escape")
    started = time.monotonic()
    try:
        result = str(asyncio.run(asyncio.wait_for(runner(), 25)))
        assert time.monotonic() - started < 12
        assert "was stopped" in result
    finally:
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass


# --- argument handling, schemas and declaration errors --------------------------------
import asyncio as _asyncio
import sys as _sys

from service.skills import tools as _skill_tools


def test_omitted_optional_arguments_never_shift_positions_or_render_none():
    build = _skill_tools._build_argv
    assert build("run.sh {a} {b}", {"a": "x"}) == ["run.sh", "x"]
    assert build("run.sh {a} {b}", {"b": "y"}) == ["run.sh", "", "y"]          # b stays 2nd
    assert build("run.sh --app={app} {x}", {"app": None, "x": "1"}) == ["run.sh", "1"]
    assert build("run.sh --app {app} {x}", {"app": "", "x": "1"}) == ["run.sh", "1"]
    assert build("run.sh --app {app}", {"app": "mail"}) == ["run.sh", "--app", "mail"]
    assert "None" not in " ".join(build("run.sh --app={app}", {"app": None}))
    assert build("run.sh {level}", {"level": 0}) == ["run.sh", "0"]            # 0 is a value


def test_boolean_flags_are_bare_and_dash_values_are_not_options():
    build = _skill_tools._build_argv
    assert build("run.sh --verbose {v}", {"v": True}) == ["run.sh", "--verbose"]
    assert build("run.sh --verbose {v}", {"v": False}) == ["run.sh"]
    assert build("run.sh {n}", {"n": -5}) == ["run.sh", "-5"]
    assert build("run.sh {name}", {"name": "--delete-all"}).startswith("(the 'name' argument")


def test_script_tools_use_this_python_pass_bare_flags_and_safe_dash_values(tmp_path, monkeypatch):
    folder = tmp_path / "skill"
    folder.mkdir()
    (folder / "t.py").write_text("import sys; print(sys.executable); print(sys.argv[1:])\n")
    monkeypatch.setattr(_skill_tools.sandbox, "available", lambda: False)
    monkeypatch.setattr(_skill_tools.sandbox, "refusal", lambda: "")
    monkeypatch.setattr(_skill_tools, "skill_environment", lambda: dict(__import__("os").environ))
    monkeypatch.setenv("PATH", "/nonexistent")
    out = _asyncio.run(_skill_tools._make_runner(folder, {"script": "t.py"}, "fx_tool")(
        verbose=True, quiet=False, note="-x", city="Oslo", unset=None))
    assert str(out).splitlines()[0] == _sys.executable
    assert "['--verbose', '--note=-x', '--city', 'Oslo']" in str(out)


def test_required_is_a_list_not_a_flag_inside_properties_and_bad_declarations_are_reported(tmp_path):
    from service.skills import Skill
    good = Skill(name="fx", description="", body="", path=tmp_path, tools=[
        {"name": "fx_one", "command": "echo {a}",
         "parameters": {"a": {"type": "string", "required": True}, "b": {"type": "string"}}}])
    bad_name = Skill(name="fy", description="", body="", path=tmp_path,
                     tools=[{"name": "my-tool", "command": "echo hi"}])
    bad_scope = Skill(name="fz", description="", body="", path=tmp_path,
                      tools=[{"name": "fz_tool", "command": "echo hi", "read_scopes": ["/"]}])
    try:
        added = _skill_tools.register_skill_tools({"fx": good, "fy": bad_name, "fz": bad_scope})
        schema = _skill_tools.REGISTRY["fx_one"].parameters
        assert schema["required"] == ["a"]
        assert all("required" not in p for p in schema["properties"].values())
        assert added == ["fx_one"]
        assert "my-tool" in bad_name.error and "invalid" in bad_name.error
        assert "read_scopes" in bad_scope.error and "too broad" in bad_scope.error
    finally:
        _skill_tools.register_skill_tools({})


def test_a_sandbox_denial_is_named_even_when_it_is_past_the_output_cap(tmp_path, monkeypatch):
    folder = tmp_path / "skill"
    folder.mkdir()
    (folder / "t.py").write_text(
        "import sys\nprint('x' * 9000)\nprint('PermissionError: [Errno 1] Operation not permitted', file=sys.stderr)\nsys.exit(1)\n")
    monkeypatch.setattr(_skill_tools.sandbox, "available", lambda: False)
    monkeypatch.setattr(_skill_tools.sandbox, "refusal", lambda: "")
    monkeypatch.setattr(_skill_tools, "skill_environment", lambda: dict(__import__("os").environ))
    out = _asyncio.run(_skill_tools._make_runner(folder, {"script": "t.py"}, "fx_tool")())
    assert str(out).startswith("(blocked by the sandbox")


def test_a_broken_sandbox_refuses_to_run_the_tool_unsandboxed(tmp_path, monkeypatch):
    folder = tmp_path / "skill"
    folder.mkdir()
    (folder / "t.py").write_text("print('ran')\n")
    monkeypatch.setattr(_skill_tools.sandbox, "available", lambda: False)
    monkeypatch.setattr(_skill_tools.sandbox, "refusal", lambda: "the sandbox could not be started")
    out = _asyncio.run(_skill_tools._make_runner(folder, {"script": "t.py"}, "fx_tool")())
    assert "was not run" in str(out) and "ran" not in str(out).replace("was not run", "")
