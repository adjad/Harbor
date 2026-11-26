"""DayCards.refresh (NotchDayView.swift) never drops a forced refresh.

The real `DayCards` enum is compiled with stub models (no network, no app):
a forced refresh that arrives while a read is in flight gets exactly one more
read, including one that arrives after the read's last check but before its
caller has resumed (the gap that used to leave Day's rows stale for a minute).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
import Foundation

@MainActor final class Probe {
    static var reads = 0
    static var onFirstRead: (() -> Void)?
    static var onLastStep: (() -> Void)?
    static var spawned: [Task<Void, Never>] = []
}
@MainActor final class AttentionModel {
    func refresh() async {
        Probe.reads += 1
        if let hook = Probe.onFirstRead { Probe.onFirstRead = nil; hook() }
        await Task.yield()
    }
}
@MainActor final class StaleRemindersModel {
    init<T>(baseURL: T) {}
    func refresh() async { await Task.yield() }
}
@MainActor final class HoursOfferModel {
    // Returns without suspending, so the read's last check follows at once.
    func refresh() async { if let hook = Probe.onLastStep { Probe.onLastStep = nil; hook() } }
}
enum HarborClient { static let baseURL = URL(string: "http://127.0.0.1:9")! }

@MainActor func forcedLater(_ now: Date) {
    Probe.spawned.append(Task { @MainActor in await DayCards.refresh(force: true, now: now) })
}

@MainActor func settle() async {
    while !Probe.spawned.isEmpty {
        let pending = Probe.spawned
        Probe.spawned = []
        for task in pending { await task.value }
    }
}

@MainActor func check(_ ok: Bool, _ what: String) {
    if !ok { print("FAIL: \(what) (reads \(Probe.reads))"); exit(1) }
}

@main struct Main {
    @MainActor static func main() async {
        var clock = Date(timeIntervalSince1970: 1_793_000_000)
        func next() -> Date { clock = clock.addingTimeInterval(120); return clock }

        // A forced refresh after the read's last check, before its caller resumes.
        Probe.reads = 0
        let gap = next()
        Probe.onLastStep = { forcedLater(gap) }
        await DayCards.refresh(force: true, now: gap)
        await settle()
        check(Probe.reads == 2, "a forced refresh in the gap still gets its read")

        // A forced refresh mid-read: one more read follows.
        Probe.reads = 0
        let mid = next()
        Probe.onFirstRead = { forcedLater(mid) }
        await DayCards.refresh(force: true, now: mid)
        await settle()
        check(Probe.reads == 2, "a forced refresh mid-read gets one more read")

        // A burst of forced refreshes mid-read: still one more read, not one each.
        Probe.reads = 0
        let burst = next()
        Probe.onFirstRead = { forcedLater(burst); forcedLater(burst); forcedLater(burst) }
        await DayCards.refresh(force: true, now: burst)
        await settle()
        check(Probe.reads == 2, "a burst mid-read is one more read")

        // Unforced within a minute of the last read: no read.
        Probe.reads = 0
        await DayCards.refresh(now: burst.addingTimeInterval(30))
        check(Probe.reads == 0, "an unforced refresh within a minute reads nothing")

        print("day cards refresh checks passed")
    }
}
'''


def _day_cards_source() -> str:
    source = (ROOT / "app/Sources/HarborApp/NotchDayView.swift").read_text()
    start = source.index("@MainActor\nenum DayCards {")
    end = source.index("    #if HARBOR_NOTCH_QA", start)
    return source[start:end] + "}\n"


def test_day_cards_region_is_only_the_coalescing_logic():
    region = _day_cards_source()
    assert "static func refresh(force: Bool = false" in region
    assert "View" not in region.replace("NotchDayView", "")


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("swiftc"), reason="macOS compiler required")
def test_forced_day_refresh_is_never_dropped(tmp_path):
    (tmp_path / "DayCards.swift").write_text("import Foundation\n" + _day_cards_source())
    (tmp_path / "Harness.swift").write_text(HARNESS)
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
    for key, leaf in [("HOME", "home"), ("HARBOR_HOME", "state"), ("TMPDIR", "tmp")]:
        (tmp_path / leaf).mkdir()
        env[key] = str(tmp_path / leaf)
    executable = tmp_path / "day-cards-checks"
    compiled = subprocess.run(
        ["/usr/bin/swiftc", "-parse-as-library", "-swift-version", "5",
         "-module-cache-path", str(tmp_path / "modules"),
         str(tmp_path / "DayCards.swift"), str(tmp_path / "Harness.swift"), "-o", str(executable)],
        env=env, cwd=tmp_path, capture_output=True, text=True, timeout=180,
    )
    assert compiled.returncode == 0, compiled.stderr
    checked = subprocess.run([str(executable)], env=env, cwd=tmp_path, capture_output=True, text=True, timeout=15)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "day cards refresh checks passed" in checked.stdout
