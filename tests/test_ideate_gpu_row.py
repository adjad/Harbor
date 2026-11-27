"""The Ideate GPU-limit row reaches Terminal only through its own Cancel-first confirmation.

Source checks (the Swift view can't run here): the row shows the whole command wrapped in the
page; its first Run in Terminal press only swaps the buttons for a confirmation (Cancel is the
default); only that confirmation's second button calls ScriptRunner; nothing in the row or in
IdeateGPULimit runs a process, an AppleScript or sudo itself; and every state is in `branches`,
so the panel refits instead of drawing one state over another."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app" / "Sources" / "HarborApp"


def _gpu_row() -> str:
    view = (APP / "NotchIdeateView.swift").read_text()
    start = view.index("private var gpuRow: some View")
    end = view.index("private var modelNote: some View", start)
    return view[start:end]


RUNNERS = ("runInTerminalConfirmed", "NSAppleScript", "Process(", "NSTask", "posix_spawn", "system(",
           "NSWorkspace", "do script", "AuthorizationExecute", "SMJobBless")


def _between(text: str, start: str, end: str) -> str:
    a = text.index(start)
    return text[a:text.index(end, a)]


def test_row_runs_only_from_its_confirmation():
    row = _gpu_row()
    confirm = _between(row, "private func gpuConfirm", "\n    }\n")
    rest = row.replace(confirm, "")
    for name in RUNNERS:
        assert name not in rest, name
    assert confirm.count("runInTerminalConfirmed") == 1
    assert 'Button("Cancel") { gpuConfirming = false }' in confirm
    assert ".keyboardShortcut(.defaultAction)" in confirm
    assert confirm.index('Button("Cancel")') < confirm.index("ScriptRunner.runInTerminalConfirmed")
    # The first press only asks.
    assert 'pill("Run in Terminal", icon: "terminal", filled: false) { gpuConfirming = true }' in row
    # The confirmation takes the buttons' place rather than drawing over them.
    assert "if open && gpuConfirming {\n                gpuConfirm(limit.command)\n            } else {" in row


def test_row_fits_the_page_and_refits():
    view = (APP / "NotchIdeateView.swift").read_text()
    row = _gpu_row()
    box = _between(row, "private func gpuCommandBox", "\n    }\n")
    assert "Text(command)" in box and ".fixedSize(horizontal: false, vertical: true)" in box
    assert ".frame(maxWidth: .infinity, alignment: .leading)" in box
    assert "ViewThatFits(in: .horizontal)" in row
    assert "MarkdownView(" not in row
    assert "(gpuConfirming ? 8 : 0)" in _between(view, "private var branches: Branches", "\n    }\n")


def test_the_limit_type_runs_nothing():
    src = (APP / "IdeateGPULimit.swift").read_text()
    code = re.sub(r"//[^\n]*", "", src)
    for name in RUNNERS:
        assert name not in code, name
    assert 'return "```bash\\n\\(command)\\n```"' in src or '"```bash\\n\\(command)\\n```"' in src
    assert 'sysctlbyname("iogpu.wired_limit_mb", &mb, &size, nil, 0)' in src   # read only: no new value


def test_a_bash_block_runs_only_after_the_card():
    md = (APP / "MarkdownView.swift").read_text()
    assert 'case "bash", "sh", "zsh", "shell": return .terminal(interpreter: nil, ext: "sh")' in \
        (APP / "ScriptRunner.swift").read_text()
    assert "if target.runsScript {\n                                confirming = true" in md
    card = md[md.index("private func confirmCard"):md.index("private func actionButton")]
    assert 'Button("Cancel") { confirming = false }' in card and ".keyboardShortcut(.defaultAction)" in card
    assert card.index('Button("Cancel")') < card.index("ScriptRunner.runInTerminalConfirmed")


def test_nothing_makes_it_permanent_or_handles_the_password():
    for path in APP.glob("Ideate*.swift"):
        text = path.read_text()
        for bad in ("sysctl.conf", "LaunchDaemons", "launchctl", "SecKeychain", "password ="):
            assert bad not in text, (path.name, bad)
