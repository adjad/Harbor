import Foundation

// FreshStart (app/Sources/HarborApp/FreshStart.swift): the one-time first-launch fresh start.
// Pure checks: a temp directory stands in for the home and an in-memory store for the defaults.
// Never touches the real ~/.moe or the real defaults domain, and calls no service.
enum FreshStartChecks {
    static func check(_ condition: @autoclosure () -> Bool, _ message: String) {
        precondition(condition(), "freshstart: " + message)
    }

    final class Store: FreshStartDefaults {
        var domain: [String: Any] = [:]
        var global: [String: Any] = ["AppleLocale": "en_US"]
        func persistentDomain(forName domainName: String) -> [String: Any]? { domainName == HarborReset.bundleID ? domain : nil }
        func removePersistentDomain(forName domainName: String) { if domainName == HarborReset.bundleID { domain = [:] } }
        func dictionaryRepresentation() -> [String: Any] { global.merging(domain) { _, app in app } }
        func removeObject(forKey defaultName: String) { domain[defaultName] = nil }
        func synchronize() -> Bool { true }
        func string(forKey defaultName: String) -> String? { domain[defaultName] as? String }
        func set(_ value: Any?, forKey defaultName: String) { domain[defaultName] = value }
    }

    static let date = Date(timeIntervalSince1970: 1_795_000_000)
    static let utc = TimeZone(identifier: "UTC")!

    static func makeHome() -> String {
        let home = FileManager.default.temporaryDirectory
            .appendingPathComponent("harbor-fresh-start-\(UUID().uuidString)").path
        let moe = home + "/.moe"
        try! FileManager.default.createDirectory(atPath: moe + "/google_workspace", withIntermediateDirectories: true)
        try! FileManager.default.createDirectory(atPath: moe + "/engine", withIntermediateDirectories: true)
        for name in FreshStart.clearedFiles + ["gmail.json", "sessions.db", "facts.db", "profile.json", "config.yaml",
                                              "mcp.json", "audit.jsonl", "google_workspace/token.json", "engine/model.bin"] {
            FileManager.default.createFile(atPath: moe + "/" + name, contents: Data("invented \(name)".utf8))
        }
        return home
    }

    static func seed(_ defaults: Store) {
        for key in HarborReset.firstRunMarkers { defaults.set(true, forKey: key) }
        defaults.set(true, forKey: DayCalendarSyncKeys.enabled)
        defaults.set("Priya's calendar", forKey: "someCustomizeChoice")
        defaults.set("Not now", forKey: "HarborGoogleNoteDismissed.sam@example.com")
    }
    enum DayCalendarSyncKeys { static let enabled = "dayCalendarSync.enabled" }

    static func exists(_ home: String, _ rel: String) -> Bool {
        FileManager.default.fileExists(atPath: home + "/.moe/" + rel)
    }

    static func run() {
        classification()
        backupNaming()
        guards()
        runsOnceAndKeeps()
        rekeyAndFailure()
        print("PASS: fresh start — runs once per id, re-key runs again, keep/clear classes, backup naming, failures still mark done, never in tests/QA")
    }

    static func classification() {
        for name in FreshStart.clearedFiles { check(FreshStart.disposition(name) == .clear, "\(name) is cleared") }
        for name in ["gmail.json", "google_workspace", "mcp.json", "config.yaml", "engine", "provisioning",
                     ".credential-generation", "sessions.db", "facts.db", "assistant.db", "profile.json",
                     "mail_memory", "cache", "backups", "audit.jsonl", "unknown-future-file.json",
                     ".fresh-start-done.json", ".reset-pending"] {
            check(FreshStart.disposition(name) == .keep, "\(name) is kept")
        }
        // Nested, empty and traversal paths never clear.
        for path in ["", ".", "..", "grants.json/x", "sub/grants.json", "../grants.json", "/grants.json"] {
            check(FreshStart.disposition(path) == .keep, "\(path) is kept")
        }
        check(FreshStart.clearedFiles.allSatisfy { !FreshStart.neverTouched.contains($0) }, "no overlap with never-touched")
    }

    static func backupNaming() {
        let name = FreshStart.backupFolderName(id: "0.1.8-fresh-1", now: date, timeZone: utc)
        check(name.hasPrefix("fresh-start-0.1.8-fresh-1-"), "prefix")
        let parts = name.dropFirst("fresh-start-0.1.8-fresh-1-".count).split(separator: "-")
        check(parts.count == 2 && parts[0].count == 8 && parts[1].count == 6
              && parts.allSatisfy { $0.allSatisfy(\.isNumber) }, "date-time suffix is YYYYMMDD-HHMMSS")
        let epoch = FreshStart.backupFolderName(id: "x", now: Date(timeIntervalSince1970: 0), timeZone: utc)
        check(epoch == "fresh-start-x-19700101-000000", "exact format: \(epoch)")
    }

    static func guards() {
        let home = makeHome()
        defer { try? FileManager.default.removeItem(atPath: home) }
        for (bundle, env, why) in [(nil as String?, [:], "unbundled"), ("com.example.other", [:], "other bundle"),
                                   (HarborReset.bundleID, ["HARBOR_HOME": "/tmp/x"], "sandboxed home"),
                                   (HarborReset.bundleID, ["HARBOR_QA_OFFSCREEN": "1"], "offscreen QA"),
                                   (HarborReset.bundleID, ["HARBOR_NOTCH_QA_ONBOARDING": "1"], "notch QA"),
                                   (HarborReset.bundleID, ["XCTestConfigurationFilePath": "/x"], "tests")] as [(String?, [String: String], String)] {
            let defaults = Store(); seed(defaults)
            let outcome = FreshStart.run(home: home, bundleID: bundle, environment: env, defaults: defaults,
                                         now: date, timeZone: utc)
            check(!outcome.ran, "does not run: \(why)")
            check(exists(home, "grants.json") && !exists(home, ".fresh-start-done.json") && !defaults.domain.isEmpty,
                  "nothing touched: \(why)")
        }
        let off = Store(); seed(off)
        check(!FreshStart.run(home: home, bundleID: HarborReset.bundleID, environment: [:], defaults: off,
                              enabled: false, now: date, timeZone: utc).ran, "the switch turns it off")
        check(exists(home, "grants.json"), "off leaves files")
    }

    static func runsOnceAndKeeps() {
        let home = makeHome()
        defer { try? FileManager.default.removeItem(atPath: home) }
        let defaults = Store(); seed(defaults)
        let outcome = FreshStart.run(home: home, bundleID: HarborReset.bundleID, environment: [:], defaults: defaults,
                                     now: date, timeZone: utc)
        check(outcome.ran && outcome.problems.isEmpty, "ran cleanly: \(outcome.problems)")
        check(outcome.moved.sorted() == FreshStart.clearedFiles.sorted(), "every settings file was moved")
        guard let backup = outcome.backup else { check(false, "has a backup folder"); return }
        check(backup.hasPrefix(home + "/.moe/backups/fresh-start-0.1.8-fresh-1-"), "backup lives in ~/.moe/backups")
        for name in FreshStart.clearedFiles {
            check(!exists(home, name), "\(name) left ~/.moe")
            check(FileManager.default.fileExists(atPath: backup + "/" + name), "\(name) is in the backup")
        }
        for kept in ["gmail.json", "sessions.db", "facts.db", "profile.json", "config.yaml", "mcp.json", "audit.jsonl",
                     "google_workspace/token.json", "engine/model.bin"] {
            check(exists(home, kept), "\(kept) kept in place")
        }
        check(!FileManager.default.fileExists(atPath: backup + "/gmail.json"), "gmail.json is never in the backup")
        // The defaults were exported first, then cleared down to the done key.
        let plist = backup + "/defaults-\(HarborReset.bundleID).plist"
        let exported = (NSDictionary(contentsOfFile: plist) as? [String: Any]) ?? [:]
        check(exported[FirstRunSetup.doneKey] as? Bool == true && exported["someCustomizeChoice"] != nil,
              "the defaults plist holds what was cleared")
        check(defaults.domain.keys.sorted() == [FreshStart.doneDefaultsKey], "only the done key is left in the domain")
        check(defaults.global["AppleLocale"] != nil, "global defaults untouched")
        let flag: (String) -> Bool = { defaults.bool($0) }
        check(FirstRunSetup.shouldShow(flag, reopens: 0), "setup shows again, from step 1")
        check(!flag(FirstRunSetup.doneKey) && !flag(FirstRunSetup.startedKey), "done and started flags are cleared")
        check(FreshStart.ranIDs(markerAt: FreshStart.markerURL(home: home)) == [FreshStart.id], "marker records the id")

        // Second launch: nothing happens, even if settings were written again.
        defaults.set(true, forKey: FirstRunSetup.doneKey)
        FileManager.default.createFile(atPath: home + "/.moe/grants.json", contents: Data("new".utf8))
        let again = FreshStart.run(home: home, bundleID: HarborReset.bundleID, environment: [:], defaults: defaults,
                                   now: date.addingTimeInterval(60), timeZone: utc)
        check(!again.ran, "does not run twice")
        check(exists(home, "grants.json") && defaults.bool(FirstRunSetup.doneKey), "a second launch changes nothing")
        // Either record alone is enough.
        try? FileManager.default.removeItem(at: FreshStart.markerURL(home: home))
        check(!FreshStart.run(home: home, bundleID: HarborReset.bundleID, environment: [:], defaults: defaults,
                              now: date, timeZone: utc).ran, "the defaults key alone stops a rerun")
    }

    static func rekeyAndFailure() {
        let home = makeHome()
        defer { try? FileManager.default.removeItem(atPath: home) }
        let defaults = Store(); seed(defaults)
        check(FreshStart.run(home: home, bundleID: HarborReset.bundleID, environment: [:], defaults: defaults,
                             id: "a-1", now: date, timeZone: utc).ran, "first id runs")
        FileManager.default.createFile(atPath: home + "/.moe/user_hours.json", contents: Data("again".utf8))
        defaults.set(true, forKey: FirstRunSetup.doneKey)
        let second = FreshStart.run(home: home, bundleID: HarborReset.bundleID, environment: [:], defaults: defaults,
                                    id: "a-2", now: date, timeZone: utc)
        check(second.ran && !exists(home, "user_hours.json") && !defaults.bool(FirstRunSetup.doneKey), "a new id runs again")
        check(FreshStart.ranIDs(markerAt: FreshStart.markerURL(home: home)) == ["a-1", "a-2"], "marker keeps earlier ids")
        check(second.backup != nil && second.backup! != "", "second run has its own folder")

        // A pending /resetharbor goes first.
        let pendingHome = makeHome()
        defer { try? FileManager.default.removeItem(atPath: pendingHome) }
        let doc = #"{"version":1,"id":"ab12","phase":"first-pass"}"#
        FileManager.default.createFile(atPath: HarborReset.markerURL(home: pendingHome).path, contents: Data(doc.utf8))
        let pending = FreshStart.run(home: pendingHome, bundleID: HarborReset.bundleID, environment: [:], defaults: Store(),
                                     now: date, timeZone: utc)
        check(!pending.ran && exists(pendingHome, "grants.json"), "waits for a pending reset")

        // Failure partway: a settings file that cannot be moved. It is noted, still marked done, and never retried.
        let failHome = makeHome()
        defer {
            try? FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: failHome + "/.moe/backups")
            try? FileManager.default.removeItem(atPath: failHome)
        }
        // A file where the backups folder must be: the folder can't be made.
        FileManager.default.createFile(atPath: failHome + "/.moe/" + FreshStart.backupsFolder, contents: Data())
        let failing = Store(); seed(failing)
        let failed = FreshStart.run(home: failHome, bundleID: HarborReset.bundleID, environment: [:], defaults: failing,
                                    now: date, timeZone: utc)
        check(failed.ran && !failed.problems.isEmpty && failed.moved.isEmpty, "failure is reported, nothing moved")
        check(exists(failHome, "grants.json"), "files stay when no backup could be made")
        check(failing.domain[FirstRunSetup.doneKey] != nil, "defaults are not cleared without an export")
        check(FreshStart.hasRun(id: FreshStart.id, home: failHome, defaults: failing), "failure still marks it done")
        check(!FreshStart.run(home: failHome, bundleID: HarborReset.bundleID, environment: [:], defaults: failing,
                              now: date, timeZone: utc).ran, "no retry loop after a failure")
    }
}

extension FreshStartChecks.Store {
    func bool(_ key: String) -> Bool { domain[key] as? Bool ?? false }
}
