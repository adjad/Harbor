import Foundation

// Contract checks for app/Sources/HarborApp/AppBugRules.swift and ResearchExportName.swift:
//   swiftc -parse-as-library app/Sources/HarborApp/{AppBugRules,ResearchExportName}.swift tests/AppBugChecks.swift
// No network, no Keychain; every value is made up.

@main
struct AppBugChecks {
    static var passed = 0

    static func check(_ ok: Bool, _ what: String) {
        if ok { passed += 1 } else { fputs("FAILED: \(what)\n", stderr); exit(1) }
    }

    static func main() {
        research()
        caps()
        ideate()
        keys()
        memory()
        exportNames()
        print("APP_BUG_CHECKS_OK \(passed)")
    }

    // MARK: - Study page caps

    static func caps() {
        check(ScalarCap.prefix("abc", 3) == "abc" && !ScalarCap.exceeds("abc", 3), "text at the cap stands")
        // One Character each, two code points each.
        let crlf = String(repeating: "\r\n", count: 10)
        check(crlf.count == 10 && ScalarCap.exceeds(crlf, 10), "CRLF counts as two code points")
        check(ScalarCap.prefix(crlf, 10).unicodeScalars.count == 10, "cut by code points")
        let accented = String(repeating: "e\u{301}", count: 8)
        check(ScalarCap.prefix(accented, 9).unicodeScalars.count == 9, "decomposed accents are cut by code points")
        let family = String(repeating: "\u{1F469}\u{200D}\u{1F393}", count: 5)
        check(family.count == 5 && ScalarCap.prefix(family, 7).unicodeScalars.count == 7, "emoji sequences too")
        check(ScalarCap.prefix("abc", 0).isEmpty, "a zero cap is empty")
    }

    // MARK: - Ideate replies

    static func ideate() {
        check(IdeateReply.applies(sentFor: "idea-a", open: "idea-a"), "the sending project takes the reply")
        check(!IdeateReply.applies(sentFor: "idea-a", open: "idea-b"), "another open project does not")
        check(!IdeateReply.applies(sentFor: "idea-a", open: nil), "the empty tab does not")
    }

    // MARK: - Replacing a saved key (a fake store; the Keychain is never touched)

    final class FakeKeys {
        var items: [String: String] = ["demo": "old"]
        var failAdd: Set<String> = []
        var failRemove: Set<String> = []
        var failRename = false

        func replace(with new: String) -> Bool {
            SecretReplacement(
                add: { account in
                    guard !self.failAdd.contains(account), self.items[account] == nil else { return false }
                    self.items[account] = new
                    return true
                },
                remove: { account in
                    guard !self.failRemove.contains(account) else { return false }
                    self.items[account] = nil
                    return true
                },
                rename: { from, to in
                    guard !self.failRename, self.items[to] == nil, let value = self.items[from] else { return false }
                    self.items[to] = value
                    self.items[from] = nil
                    return true
                }).replace(account: "demo")
        }
    }

    static func keys() {
        let spare = SecretReplacement.spare(for: "demo")
        let plain = FakeKeys()
        check(plain.replace(with: "new") && plain.items == ["demo": "new"], "the new key replaces the old")
        let first = FakeKeys()
        first.items = [:]
        check(first.replace(with: "new") && first.items == ["demo": "new"], "a first save")
        let stale = FakeKeys()
        stale.items[spare] = "left over"
        check(stale.replace(with: "new") && stale.items == ["demo": "new"], "a leftover spare is cleared")
        let noAdd = FakeKeys()
        noAdd.failAdd = [spare]
        check(!noAdd.replace(with: "new") && noAdd.items == ["demo": "old"], "a failed add keeps the old key")
        let noRemove = FakeKeys()
        noRemove.failRemove = ["demo"]
        check(!noRemove.replace(with: "new") && noRemove.items == ["demo": "old"], "a failed remove keeps the old key")
        let noRename = FakeKeys()
        noRename.failRename = true
        check(noRename.replace(with: "new") && noRename.items == ["demo": "new"], "a failed rename adds in place")
        let neither = FakeKeys()
        neither.failRename = true
        neither.failAdd = ["demo"]
        check(!neither.replace(with: "new") && neither.items == [spare: "new"], "the new key is never lost")
    }

    // MARK: - Memory's empty lists

    static func memory() {
        let line = MemoryEmptyText.line
        check(line("active", "  ").hasPrefix("Nothing saved yet"), "an empty Saved list")
        check(line("superseded", "").hasPrefix("No corrections yet"), "an empty Corrections list")
        check(!line("superseded", "").contains("Nothing saved"), "Corrections never says nothing is saved")
        check(line("proposed", "").hasPrefix("Nothing to review"), "an empty Waiting list")
        check(line("deferred", "").hasPrefix("Nothing set aside"), "an empty Later list")
        check(!line("deferred", "").contains("Nothing to review"), "Later never says there is nothing to review")
        check(line("deferred", " tea ") == "No memories match “tea”.", "a search names what it looked for")
    }

    // MARK: - Research export names (a throwaway folder, never Downloads)

    static func exportNames() {
        let files = FileManager.default
        // Under the gate's own scratch folder (the runner's per-gate TMPDIR; the QA sandbox
        // denies the system temporary folder), the system one only when run by hand.
        let scratch = ProcessInfo.processInfo.environment["TMPDIR"].flatMap { $0.hasPrefix("/") ? $0 : nil }
        let base = scratch.map { URL(fileURLWithPath: $0, isDirectory: true) } ?? files.temporaryDirectory
        let dir = base.appendingPathComponent("app-bug-export-\(UUID().uuidString)")
        defer { try? files.removeItem(at: dir) }
        check((try? files.createDirectory(at: dir, withIntermediateDirectories: true)) != nil, "a scratch folder")
        let first = ResearchExportName.unused("Rivers", in: dir)
        check(first.lastPathComponent == "Rivers.md", "the plain name when it is free")
        check((try? Data("edited".utf8).write(to: first)) != nil, "write the first export")
        let second = ResearchExportName.unused("Rivers", in: dir)
        check(second.lastPathComponent == "Rivers 2.md", "an existing file is never replaced")
        check((try? files.createSymbolicLink(atPath: second.path, withDestinationPath: "/nowhere/at/all")) != nil,
              "a dangling link")
        check(ResearchExportName.unused("Rivers", in: dir).lastPathComponent == "Rivers 3.md",
              "a link that points nowhere is taken too")
        check((try? String(contentsOf: first, encoding: .utf8)) == "edited", "the earlier export is unchanged")
    }

    // MARK: - Research reconnects

    static func research() {
        check(ResearchReconnect.isGone(streamMessage: "Research returned 404."), "404 is gone")
        check(ResearchReconnect.isGone(streamMessage: "Research returned 410."), "410 is gone")
        check(!ResearchReconnect.isGone(streamMessage: "Research returned 500."), "a server error is retried")
        check(!ResearchReconnect.isGone(streamMessage: "The network connection was lost."), "a drop is retried")
        var failures = 0
        for _ in 0..<ResearchReconnect.maxFailures {
            failures = ResearchReconnect.failures(after: failures, readSnapshot: false, newEvents: false)
        }
        check(failures == ResearchReconnect.maxFailures, "consecutive failures reach the cap")
        check(ResearchReconnect.failures(after: failures, readSnapshot: true, newEvents: false) == 0,
              "reading the job starts again")
        check(ResearchReconnect.failures(after: failures, readSnapshot: false, newEvents: true) == 0,
              "a new event starts again")
    }
}
