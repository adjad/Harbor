"""A role, or a behavior runtime, one test sets up is not the next test's (tests/conftest.py).

Synthetic model name only; the overlay lives in the test's own temp folder."""
from __future__ import annotations

from service import config

_seen: dict = {}


def test_a_test_changes_the_fast_role():
    _seen["before"] = config.role_to_model("fast")
    config.set_role("fast", "changed-synthetic-role")
    assert config.role_to_model("fast") == "changed-synthetic-role"


def test_the_next_test_has_the_role_it_had_before():
    assert "before" in _seen                           # runs after the test above
    assert config.role_to_model("fast") == _seen["before"]


def test_a_test_starts_the_behavior_runtime(monkeypatch):
    from service.assistant import behavior_runtime
    monkeypatch.setenv("HARBOR_BEHAVIOR", "1")
    _seen["runtime"] = behavior_runtime._runtime
    assert behavior_runtime.get_runtime() is not None


def test_the_next_test_does_not_inherit_it():
    from service.assistant import behavior_runtime
    assert "runtime" in _seen
    assert behavior_runtime._runtime is _seen["runtime"]


def test_a_test_saves_an_engine_mode():
    from service.engine import mode
    from service.config import _save_overlay
    _seen["mode"] = mode.configured_mode()
    _save_overlay({"engine": {"mode": mode.EXTERNAL}})
    assert mode.configured_mode() == mode.EXTERNAL     # read back from the file it was saved to


def test_the_next_test_has_the_engine_mode_it_had_before():
    from service.engine import mode
    assert "mode" in _seen
    assert mode.configured_mode() == _seen["mode"]
