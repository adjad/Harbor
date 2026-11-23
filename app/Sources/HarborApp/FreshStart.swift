import Foundation

/// A one-time "fresh start" on the first launch of a release: the app's own settings go back
/// to a first run (setup and Customize start at step 1) while chats, memory, Google sign-ins,
/// models and mail/calendar caches stay. Nothing is deleted: settings files are MOVED into a
/// dated folder under ~/.moe/backups and the app's defaults are exported there first.
///
/// Built on HarborReset (ResetHarbor.swift): the same defaults clearing and first-run markers, but
/// none of /resetharbor's service erase, Keychain or tccutil parts. It runs once per `id`, not per
/// version, so a Mac that already has this version still gets it. Turn it off with `enabled`,
/// or re-key `id` for a later release to run it again.
enum FreshStart {
    /// The switch. Same source for the arm64 and Intel builds.
    static let enabled = true
    /// What the "once" is keyed to.
    static let id = "0.1.8-fresh-1"

    /// The record that it ran, in Harbor's home. It is not a setting, so the clear never moves it.
    static let markerName = ".fresh-start-done.json"
    /// A second record in the app's defaults, written after the clear. Either one means "ran".
    static let doneDefaultsKey = "harbor.freshStart.doneID"
    static let backupsFolder = "backups"

    // MARK: What is cleared and what is kept

    /// Settings files at the top level of ~/.moe that are moved to the backup. Exactly these
    /// names: everything else in the home is kept, including files a later version adds.
    static let clearedFiles = [
        "act_for_me.json",            // "Act for me" choices
        "act_for_me_asked.json",      // its "Not now" notes
        "user_hours.json",            // working and quiet hours
        "user_hours.last-good.json",  // the copy of the above
        "hours_offers.json",          // offers made about those hours
        "access_mode.json",           // the chosen access mode (back to the default)
        "grants.json",                // standing "always allow" grants
        "brief_state.json",           // when the Brief last went out
    ]
    /// Never moved, whatever the list above says: sign-ins and the keys they use.
    static let neverTouched = [
        "gmail.json", "google_workspace", "mcp.json", ".credential-generation",
        "provisioning", "engine", "config.yaml",
    ]
    /// What stays, by group (for the handoff and the docs; classification is `disposition`).
    static let keptGroups = [
        "Chats, sessions, memory, facts and profile (sessions.db, facts.db, assistant.db, profile.json, research.db, behavior*.db)",
        "Google sign-ins (gmail.json, google_workspace) and added tools (mcp.json, skills)",
        "Models, the engine and the model/engine choice (engine/, config.yaml, provisioning)",
        "Mail, calendar and Messages caches (mail_memory, cache, daily-workspace, attention, proactive, use-log)",
        "Logs and diagnostics, receipts, and check-in state",
    ]

    enum Disposition: Equatable { case clear, keep }

    /// `relativePath` is a path inside ~/.moe. Only a plain top-level name on the list is cleared.
    static func disposition(_ relativePath: String) -> Disposition {
        guard !relativePath.isEmpty, !relativePath.contains("/"), relativePath != ".", relativePath != ".." else {
            return .keep
        }
        if neverTouched.contains(relativePath) { return .keep }
        return clearedFiles.contains(relativePath) ? .clear : .keep
    }

    // MARK: Names and records

    /// fresh-start-<id>-<YYYYMMDD-HHMMSS>
    static func backupFolderName(id: String = FreshStart.id, now: Date, timeZone: TimeZone = .current) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.timeZone = timeZone
        formatter.dateFormat = "yyyyMMdd-HHmmss"
        return "fresh-start-\(id)-\(formatter.string(from: now))"
    }

    static func markerURL(home: String) -> URL {
        URL(fileURLWithPath: home).appendingPathComponent(".moe").appendingPathComponent(markerName)
    }

    /// The ids that ran, read from the marker file (nil or junk reads as none).
    static func ranIDs(markerAt url: URL) -> [String] {
        guard let data = try? Data(contentsOf: url),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let ids = object["ids"] as? [String] else { return [] }
        return ids
    }

    static func hasRun(id: String, home: String, defaults: FreshStartDefaults) -> Bool {
        ranIDs(markerAt: markerURL(home: home)).contains(id) || defaults.string(forKey: doneDefaultsKey) == id
    }

    /// Where it must not run: a build without Harbor's own bundle id, a sandboxed home, and the
    /// notch QA and test modes.
    static func environmentAllows(bundleID: String?, environment: [String: String]) -> Bool {
        guard bundleID == HarborReset.bundleID else { return false }
        for key in environment.keys where key == "HARBOR_HOME" || key == "HARBOR_QA_OFFSCREEN"
            || key.hasPrefix("HARBOR_NOTCH_QA") || key == "XCTestConfigurationFilePath" {
            return false
        }
        return true
    }

    // MARK: Running it

    struct Outcome: Equatable {
        enum Result: Equatable {
            case skipped(String)
            case ran
        }
        var result: Result
        var moved: [String] = []
        var problems: [String] = []
        var backup: String?
        var ran: Bool { result == .ran }
    }

    /// Runs the fresh start once for `id`. Never throws and never loops: it records "ran" before
    /// it touches anything, so a failure partway is logged, noted in the marker and not retried.
    @discardableResult
    static func run(
        home: String = NSHomeDirectory(),
        bundleID: String? = Bundle.main.bundleIdentifier,
        environment: [String: String] = ProcessInfo.processInfo.environment,
        defaults: FreshStartDefaults = UserDefaults.standard,
        id: String = FreshStart.id,
        enabled: Bool = FreshStart.enabled,
        now: Date = Date(),
        timeZone: TimeZone = .current
    ) -> Outcome {
        guard enabled else { return Outcome(result: .skipped("off")) }
        guard environmentAllows(bundleID: bundleID, environment: environment), let bundleID else {
            return Outcome(result: .skipped("not Harbor's own app, or a test or QA run"))
        }
        if hasRun(id: id, home: home, defaults: defaults) { return Outcome(result: .skipped("already ran")) }
        // A committed /resetharbor finishes first; that launch is a first run anyway.
        if case .valid = HarborReset.readMarker(HarborReset.markerURL(home: home).path) {
            return Outcome(result: .skipped("a reset is pending"))
        }

        let fm = FileManager.default
        let moe = URL(fileURLWithPath: home).appendingPathComponent(".moe")
        var outcome = Outcome(result: .ran)
        // Marked first, in both places: a crash from here on must not run it a second time.
        try? fm.createDirectory(at: moe, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let marked = writeMarker(id: id, backup: nil, problems: [], at: markerURL(home: home), now: now)
        defaults.set(id, forKey: doneDefaultsKey)
        _ = defaults.synchronize()
        if !marked { outcome.problems.append("Couldn't write \(markerName); the defaults key records it instead.") }

        // The backup folder, with a numeric suffix if the name is taken.
        let backupsRoot = moe.appendingPathComponent(backupsFolder)
        let baseName = backupFolderName(id: id, now: now, timeZone: timeZone)
        var backup = backupsRoot.appendingPathComponent(baseName)
        var backupReady = false
        do {
            try fm.createDirectory(at: backupsRoot, withIntermediateDirectories: true,
                                   attributes: [.posixPermissions: 0o700])
            var suffix = 2
            while fm.fileExists(atPath: backup.path) {
                backup = backupsRoot.appendingPathComponent("\(baseName)-\(suffix)")
                suffix += 1
            }
            try fm.createDirectory(at: backup, withIntermediateDirectories: false,
                                   attributes: [.posixPermissions: 0o700])
            backupReady = true
            outcome.backup = backup.path
        } catch {
            outcome.problems.append("Couldn't make the backup folder, so no settings file was moved.")
        }

        // The defaults are exported before they are cleared. No export, no clear.
        var exported = false
        if backupReady {
            if let domain = defaults.persistentDomain(forName: bundleID) {
                do {
                    let data = try PropertyListSerialization.data(fromPropertyList: domain, format: .xml, options: 0)
                    try data.write(to: backup.appendingPathComponent("defaults-\(bundleID).plist"), options: .atomic)
                    exported = true
                } catch {
                    outcome.problems.append("Couldn't export Harbor's defaults, so they were not cleared.")
                }
            } else {
                exported = true   // nothing stored: nothing to export or clear
            }
        }

        if backupReady {
            for name in clearedFiles where disposition(name) == .clear {
                let source = moe.appendingPathComponent(name)
                // lstat: a link is moved as the link itself, never followed.
                guard (try? fm.attributesOfItem(atPath: source.path)) != nil else { continue }
                do {
                    try fm.moveItem(at: source, to: backup.appendingPathComponent(name))
                    outcome.moved.append(name)
                } catch {
                    outcome.problems.append("Couldn't move \(name).")
                }
            }
        }

        if exported {
            HarborReset.clearDefaults(defaults, domain: bundleID)
        }
        // Written again: the clear removed the first one.
        defaults.set(id, forKey: doneDefaultsKey)
        _ = defaults.synchronize()
        if !writeMarker(id: id, backup: outcome.backup, problems: outcome.problems,
                        at: markerURL(home: home), now: now) {
            outcome.problems.append("Couldn't update \(markerName).")
        }
        NSLog("%@", "Harbor fresh start \(id): moved \(outcome.moved.count) files, \(outcome.problems.count) problems")
        return outcome
    }

    /// Adds `id` to the marker (keeping earlier ids) and records the last run. False on failure.
    @discardableResult
    static func writeMarker(id: String, backup: String?, problems: [String], at url: URL, now: Date) -> Bool {
        var ids = ranIDs(markerAt: url)
        if !ids.contains(id) { ids.append(id) }
        var document: [String: Any] = ["version": 1, "ids": ids, "last": id,
                                       "at": Int(now.timeIntervalSince1970), "problems": Array(problems.prefix(20))]
        if let backup { document["backup"] = backup }
        guard let data = try? JSONSerialization.data(withJSONObject: document, options: [.sortedKeys]) else { return false }
        do {
            try data.write(to: url, options: .atomic)
            try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
            return true
        } catch {
            return false
        }
    }
}

/// What the fresh start needs from UserDefaults, so its checks run on an in-memory store.
protocol FreshStartDefaults: HarborDefaultsStore {
    func string(forKey defaultName: String) -> String?
    func set(_ value: Any?, forKey defaultName: String)
}

extension UserDefaults: FreshStartDefaults {}
