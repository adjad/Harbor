"""Tests here that need the private training data skip instead of failing.

The datasets, eval sets and model outputs these scripts read are kept in a private
Hugging Face dataset (see README.md), with the same rN/... layout as this folder.
DATA_FILES.txt lists every file in it. A test (or a test module's import) that
fails only because one of those files is missing is reported as skipped, naming
the file. A missing file that is NOT in that list (a wrong path, say) still fails.

Put the dataset's rN folders over this folder to run everything.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
_DATA = {line.strip() for line in (HERE / "DATA_FILES.txt").read_text().splitlines() if line.strip()}
_BY_ROUND = {(p.split("/", 1)[0], p.rsplit("/", 1)[-1]) for p in _DATA}
_MISSING = re.compile(r"No such file or directory: \\?'([^'\\]+)\\?'")


def _private_data(path: str) -> str | None:
    """`path` relative to this folder when it names a file kept only in the private dataset."""
    p = Path(path)
    if not p.is_absolute():
        p = (Path.cwd() / p)
    try:
        rel = p.resolve().relative_to(HERE).as_posix()
    except ValueError:
        return None
    if rel in _DATA:
        return rel
    round_ = rel.split("/", 1)[0]
    return rel if (round_, rel.rsplit("/", 1)[-1]) in _BY_ROUND else None


def _only_private_data(text: str) -> list[str] | None:
    found = _MISSING.findall(text)
    named = [_private_data(f) for f in found]
    return sorted(set(named)) if found and all(named) else None


def _reason(files: list[str]) -> str:
    return "needs private training data: " + ", ".join(files) + " (see docs/training/README.md)"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.failed and call.excinfo is not None:
        files = _only_private_data(str(call.excinfo.value) + "\n" + str(call.excinfo.getrepr(style="long")))
        if files:
            report.outcome = "skipped"
            report.longrepr = (str(item.path), item.location[1] or 0, "Skipped: " + _reason(files))


@pytest.hookimpl(hookwrapper=True)
def pytest_make_collect_report(collector):
    outcome = yield
    report = outcome.get_result()
    if report.failed:
        files = _only_private_data(str(report.longrepr))
        if files:
            report.outcome = "skipped"
            report.longrepr = (str(collector.path), 0, "Skipped: " + _reason(files))
