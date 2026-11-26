"""--changed-since (affected) Simulation QA: selection rules, and the release path untouched.

Synthetic file trees in tmp_path with a stand-in git; nothing runs a real gate.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from scripts import qa_affected
from scripts import run_simulation_qa as simqa

ROOT = Path(__file__).resolve().parents[1]
SHA = "b" * 40

SIMULATION_STUB = '''
def gates(build):
    for name, sources, contract in (
        ("widget-rules", ["WidgetRules.swift"], "WidgetRulesChecks.swift"),
    ):
        pass
'''


def _tree(root: Path, files: dict[str, str]) -> list[str]:
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text, encoding="utf-8")
    return sorted(files)


def _qa(root: Path, tracked: list[str], changed: list[str], *, runner_diff: str = "",
        untracked: list[str] = ()):
    tests = sorted(p for p in tracked if p.startswith("tests/") and Path(p).name.startswith("test_")
                   and p.endswith(".py"))

    def git(*args: str) -> str:
        if args[0] == "rev-parse":
            return SHA
        if args[:2] == ("ls-files", "-z"):
            return "\0".join(tracked)
        if args[:2] == ("ls-files", "--others"):
            return "\0".join(untracked)
        if args[:2] == ("diff", "--name-only"):
            return "\0".join(changed)
        if args[0] == "diff":
            return runner_diff
        raise AssertionError(args)

    def native(build: Path):
        return [
            ("native/clock-compile", ["/usr/bin/swiftc", "app/Sources/HarborApp/Clock.swift",
                                      "tests/ClockChecks.swift", "-o", str(build / "clock")]),
            ("native/clock-contract", [str(build / "clock")]),
            ("native/panel-contract", ["/bin/bash", "scripts/test_panel.sh"]),
        ]

    return SimpleNamespace(
        ROOT=root, _git=git, _selected_tests=lambda profiles: list(tests),
        _NATIVE_GATE_DEPENDENCIES={"native/clock-contract": "native/clock-compile"},
        _CANONICAL_NATIVE_GATES=native, _native_gates=native, TRUSTED_SWIFTC="/usr/bin/swiftc",
    )


BASE = {
    "build-support/simulation.py": SIMULATION_STUB,
    "service/__init__.py": "",
    "service/leaf.py": "VALUE = 1\n",
    "service/middle.py": "from service.leaf import VALUE\n",
    "service/hub.py": "def later():\n    from service import leaf\n    return leaf\n",
    "service/orphan.py": "X = 1\n",
    "tests/test_middle.py": "from service.middle import VALUE\n\ndef test_v():\n    assert VALUE == 1\n",
    "tests/test_hub.py": "import service.hub\n",
    "tests/test_lazy_leaf.py": "def test_it():\n    from service import leaf\n",
    "tests/test_unrelated.py": "def test_ok():\n    pass\n",
    "tests/test_clock_source.py": "SOURCE = 'Clock.swift'\n",
    "app/Sources/HarborApp/Clock.swift": "struct Clock {}\n",
    "app/Sources/HarborApp/Widget.swift": "struct Widget {}\n",
    "app/Sources/HarborApp/WidgetRules.swift": "struct WidgetRules {}\n",
    "app/Sources/HarborApp/Panel.swift": "struct Panel {}\n",
    "tests/ClockChecks.swift": "",
    "tests/WidgetRulesChecks.swift": "",
    "scripts/test_panel.sh": 'find "$root/app/Sources/HarborApp" -name "*.swift"\n',
    "conftest.py": "",
    "README.md": "notes\n",
}


def _select(tmp_path: Path, changed: list[str], **kwargs) -> qa_affected.Selection:
    tracked = _tree(tmp_path, BASE)
    return qa_affected.select(_qa(tmp_path, tracked, changed, **kwargs), "main")


def test_python_change_selects_only_tests_that_reach_it(tmp_path: Path) -> None:
    selection = _select(tmp_path, ["service/middle.py"])
    assert not selection.full_fallback
    assert selection.tests == ["tests/test_middle.py"]
    assert selection.native_names == []
    assert selection.reasons["tests/test_middle.py"] == ["uses service/middle.py directly"]


def test_import_chain_and_a_tests_own_lazy_import_are_followed(tmp_path: Path) -> None:
    selection = _select(tmp_path, ["service/leaf.py"])
    # Eager chain leaf <- middle <- test_middle, and the test's own in-function import.
    assert selection.tests == ["tests/test_lazy_leaf.py", "tests/test_middle.py"]
    # A product module's in-function import (service/hub.py) is the documented limit.
    assert "tests/test_hub.py" not in selection.tests
    assert "service/middle.py" in selection.reasons["tests/test_middle.py"][0]


def test_a_changed_test_selects_itself(tmp_path: Path) -> None:
    assert _select(tmp_path, ["tests/test_unrelated.py"]).tests == ["tests/test_unrelated.py"]


def test_swift_change_selects_its_compile_contract_pair_and_name_readers(tmp_path: Path) -> None:
    selection = _select(tmp_path, ["app/Sources/HarborApp/Clock.swift"])
    assert selection.tests == ["tests/test_clock_source.py"]
    # The compile list names it; its contract rides along; the folder-scanning script too.
    assert selection.native_names == ["native/clock-compile", "native/clock-contract",
                                      "native/panel-contract"]
    assert selection.reasons["native/clock-contract"] == ["paired compile/contract gate"]


def test_simulation_wrapper_compile_lists_are_used(tmp_path: Path) -> None:
    selection = _select(tmp_path, ["app/Sources/HarborApp/WidgetRules.swift"])
    assert not selection.full_fallback
    assert {"native/widget-rules-compile", "native/widget-rules-contract"} <= set(selection.native_names)
    commands = dict(selection.native_gates(tmp_path / "build"))
    compile_command = commands["native/widget-rules-compile"]
    assert "app/Sources/HarborApp/WidgetRules.swift" in compile_command
    assert "tests/WidgetRulesChecks.swift" in compile_command
    assert compile_command[-1] == "build-support/FixtureTemporaryDirectory.swift"
    assert "native/clock-compile" not in commands


@pytest.mark.parametrize("changed, why", [
    (["conftest.py"], "shared QA, safety or packaging infrastructure"),
    (["build-support/simulation.py"], "shared QA, safety or packaging infrastructure"),
    (["scripts/qa_affected.py"], "shared QA, safety or packaging infrastructure"),
    (["service/safety/policy.py"], "shared QA, safety or packaging infrastructure"),
    (["scripts/package_app.sh"], "shared QA, safety or packaging infrastructure"),
    (["service/orphan.py"], "no gate covers it"),
    (["README.md"], "non-code file"),
    (["service/removed.py"], "deleted or renamed"),
    ([], "no changed paths"),
])
def test_unsure_changes_fall_back_to_every_gate(tmp_path: Path, changed, why) -> None:
    selection = _select(tmp_path, ["service/middle.py", *changed] if changed else [])
    assert selection.full_fallback
    assert any(why in reason for reason in selection.fallback_reasons)
    assert selection.tests == sorted(p for p in BASE if p.startswith("tests/test_"))
    assert selection.native_names == ["native/clock-compile", "native/clock-contract",
                                      "native/panel-contract", "native/widget-rules-compile",
                                      "native/widget-rules-contract"]


def test_fallback_summary_names_the_set_and_says_it_is_no_release_run(tmp_path: Path) -> None:
    text = "\n".join(qa_affected.describe(_select(tmp_path, ["conftest.py"])))
    assert "NOT a release gate" in text
    assert "every gate this runner selects for the full profile" in text
    assert "not a release run" in text
    assert "FULL gate set" not in text


def test_a_module_conftest_imports_runs_every_gate(tmp_path: Path) -> None:
    # tests/conftest.py imports service.middle in a fixture; middle imports leaf at module level.
    # service.hub only reaches leaf through its own function body, which is not followed.
    files = {**BASE, "tests/conftest.py": "def fixture():\n    from service import middle, hub\n"}
    tracked = _tree(tmp_path, files)
    for path in ("service/middle.py", "service/leaf.py", "service/hub.py"):
        selection = qa_affected.select(_qa(tmp_path, tracked, [path]), "main")
        assert selection.full_fallback, path
        assert "imported by conftest" in selection.fallback_reasons[0]
    files["tests/conftest.py"] = "def fixture():\n    from service import hub\n"
    tracked = _tree(tmp_path, files)
    selection = qa_affected.select(_qa(tmp_path, tracked, ["service/leaf.py"]), "main")
    assert not selection.full_fallback


def test_a_data_file_runs_every_gate_even_when_a_test_names_it(tmp_path: Path) -> None:
    files = {**BASE, "service/data/rules.json": "{}\n",
             "tests/test_rules.py": "PATH = 'service/data/rules.json'\n"}
    tracked = _tree(tmp_path, files)
    selection = qa_affected.select(_qa(tmp_path, tracked, ["service/data/rules.json"]), "main")
    assert selection.full_fallback
    assert "non-code file" in selection.fallback_reasons[0]


def test_runner_manifest_lines_alone_do_not_force_the_full_set(tmp_path: Path) -> None:
    diff = ('--- a/scripts/run_simulation_qa.py\n+++ b/scripts/run_simulation_qa.py\n'
            '@@ -10,0 +11,2 @@\n+    # A new reviewed module\n+    "tests/test_unrelated.py",\n')
    selection = _select(tmp_path, ["scripts/run_simulation_qa.py"], runner_diff=diff)
    assert not selection.full_fallback
    assert selection.tests == ["tests/test_unrelated.py"]


def test_runner_logic_changes_force_the_full_set(tmp_path: Path) -> None:
    diff = '@@ -1 +1 @@\n-TRUSTED_PATH = "/usr/bin"\n+TRUSTED_PATH = "/usr/local/bin"\n'
    selection = _select(tmp_path, ["scripts/run_simulation_qa.py"], runner_diff=diff)
    assert selection.full_fallback
    assert "runner logic changed" in selection.fallback_reasons[0]


def test_untracked_local_dot_folder_is_not_a_change(tmp_path: Path) -> None:
    selection = _select(tmp_path, ["service/middle.py"],
                        untracked=[".toolstate/session.json", "service/new_leaf.py"])
    assert ".toolstate/session.json" not in selection.changed
    assert "service/new_leaf.py" in selection.changed      # unmapped new file -> fail safe
    assert selection.full_fallback


def test_untracked_file_in_a_tracked_dot_folder_still_counts(tmp_path: Path) -> None:
    tracked = _tree(tmp_path, {**BASE, ".github/workflows/ci.yml": "on: push\n"})
    selection = qa_affected.select(
        _qa(tmp_path, tracked, ["service/middle.py"], untracked=[".github/workflows/new.yml"]), "main")
    assert ".github/workflows/new.yml" in selection.changed
    assert selection.full_fallback


def test_option_shaped_ref_is_refused(tmp_path: Path) -> None:
    tracked = _tree(tmp_path, BASE)
    with pytest.raises(ValueError):
        qa_affected.select(_qa(tmp_path, tracked, []), "--output=/tmp/x")


def test_the_real_simulation_wrapper_table_parses() -> None:
    table = qa_affected.wrapper_gate_table((ROOT / "build-support/simulation.py").read_text(encoding="utf-8"))
    assert table and all(contract.endswith("Checks.swift") for _name, _sources, contract in table)


def test_infrastructure_patterns_cover_the_release_and_safety_files() -> None:
    for path in ("tests/conftest.py", "build-support/pipeline.py", "scripts/harbor-build",
                 "service/safety/redaction.py", "service/agent/approver.py",
                 "tests/build_pipeline/pipeline_checks.py", "requirements-dev.txt"):
        assert qa_affected.is_full_trigger(path), path
    assert not qa_affected.is_full_trigger("service/study/find.py")


# ---- the release path stays exactly as strict -------------------------------------------

def test_release_path_runs_every_reviewed_gate(monkeypatch, capsys) -> None:
    """Default (full) selection is the whole manifest plus every native gate, nothing filtered."""
    expected = "c" * 40
    monkeypatch.setattr(simqa, "_git", lambda *args: expected if args[:2] == ("rev-parse", "HEAD") else "")
    monkeypatch.setattr(sys, "argv", ["run_simulation_qa.py", "--expected-sha", expected, "--list"])
    assert simqa.main() == 0
    listed = capsys.readouterr().out.split("excluded live/mutating commands:")[0].split()
    native = [name for name, _ in simqa._CANONICAL_NATIVE_GATES(Path("/tmp/harbor-simqa-list"))]
    assert listed == sorted(simqa.SAFE_FULL_TESTS) + native
    assert "Changed-only" not in " ".join(listed)


def test_pipeline_and_wrapper_never_use_changed_only_selection() -> None:
    pipeline = (ROOT / "build-support/pipeline.py").read_text(encoding="utf-8")
    assert '"--profile", "full", "--report", report]' in pipeline
    for path in ("build-support/pipeline.py", "build-support/simulation.py", "scripts/harbor-build"):
        text = (ROOT / path).read_text(encoding="utf-8")
        assert "changed-since" not in text and "changed_since" not in text and "qa_affected" not in text


def test_changed_since_refuses_profile_and_the_build_wrapper(monkeypatch) -> None:
    expected = "d" * 40
    monkeypatch.setattr(simqa, "_git", lambda *args: expected)
    monkeypatch.setattr(sys, "argv", ["run_simulation_qa.py", "--expected-sha", expected,
                                      "--changed-since", "main", "--profile", "full", "--list"])
    with pytest.raises(SystemExit, match="2"):
        simqa.main()
    # build-support/simulation.py swaps in its own gate list; affected mode must refuse there.
    monkeypatch.setattr(simqa, "_native_gates", lambda build: [])
    monkeypatch.setattr(sys, "argv", ["run_simulation_qa.py", "--expected-sha", expected,
                                      "--changed-since", "main", "--list"])
    with pytest.raises(SystemExit, match="2"):
        simqa.main()


def test_affected_report_is_marked_and_rejected_as_release_evidence(monkeypatch, tmp_path, capsys) -> None:
    expected = "e" * 40
    report = tmp_path / "report.json"
    selection = qa_affected.Selection(
        ref="main", ref_sha="f" * 40, changed=["tests/test_one.py"], tests=["tests/test_one.py"],
        native_names=[], reasons={"tests/test_one.py": ["changed"]}, full_fallback=False,
        native_gates=lambda build: [])
    monkeypatch.setattr(qa_affected, "select", lambda qa, ref: selection)
    monkeypatch.setattr(simqa, "_native_gates", simqa._native_gates)   # restore after the swap
    monkeypatch.setattr(simqa, "_dependencies", lambda: {})
    monkeypatch.setattr(simqa, "_git", lambda *args: expected if args[:2] == ("rev-parse", "HEAD") else "")
    ran = []

    def run_gates(tests, run_native, jobs):
        ran.append((tests, run_native))
        return [simqa.GateResult("tests/test_one.py", ["python"], 0, 0.5, 1, 0, 0, "", "")]

    monkeypatch.setattr(simqa, "_run_gates", run_gates)
    monkeypatch.setattr(sys, "argv", ["run_simulation_qa.py", "--expected-sha", expected,
                                      "--changed-since", "main", "--report", str(report)])
    assert simqa.main() == 0
    assert ran == [(["tests/test_one.py"], False)]
    out = capsys.readouterr().out
    summary = json.loads(out[out.index("{\n"):out.index("\n}\n") + 2])
    assert summary["profiles"] == ["affected"]
    assert summary["release_gate"] is False
    assert "NOT a release gate" in summary["notice"]
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["profiles"] == ["affected"]
    assert payload["selection"]["release_gate"] is False
    assert "NOT a release gate" in payload["selection"]["notice"]

    sys.path.insert(0, str(ROOT / "build-support"))
    try:
        import pipeline
    finally:
        sys.path.pop(0)
    with pytest.raises(pipeline.BuildError):
        pipeline.validate_simulation(payload, expected)
