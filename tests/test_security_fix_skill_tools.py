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
