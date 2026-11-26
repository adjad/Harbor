import AppKit
import Foundation

/// macOS's Quit & Reopen, after a privacy switch (Full Disk Access, Screen
/// Recording, Accessibility…) is turned on for Harbor in System Settings.
///
/// System Settings sends Harbor a quit Apple Event and reopens it only if it
/// quits promptly. Harbor's quit waits for its engine and service to stop (about
/// 7 s, up to 20 s), and macOS then never reopened it: seen in the unified log
/// on rc1-beta1 (2026-11-02 21:32, "Handling Quit AppleEvent", NSTerminateLater,
/// exit 7 s later, no launch after). So when that quit came from System
/// Settings, Harbor reopens itself once it has gone (HarborReset.relaunchArguments).
/// A second open while macOS's own reopen is running only brings that one forward.
enum PrivacyRelaunch {
    /// When Harbor last opened a privacy page of System Settings, or asked macOS
    /// for a privacy permission (seconds since 1970).
    static let paneOpenedKey = "harbor.privacyPaneOpenedAt"
    /// How long that counts: long enough to find the switch and type a password.
    static let paneWindow: TimeInterval = 15 * 60

    /// What asked Harbor to quit.
    enum Quit: Equatable {
        /// Harbor's own Quit, ⌘Q or a quit it makes itself: no Apple Event.
        case notAppleEvent
        /// A quit Apple Event, with what is known about the program that sent it
        /// and its quit reason (`kAEQuitReason`, 'why?') as a four-character code.
        case appleEvent(senderBundleID: String?, senderPath: String?, reason: String? = nil)
    }

    /// Quit reasons that mean the Mac is logging out, restarting, shutting down
    /// or sleeping: kAELogOut, kAEReallyLogOut, kAEShowRestartDialog,
    /// kAEShowShutdownDialog, kAERestart, kAEShutDown, kAEShowPowerDialog, kAESleep.
    static let shutdownReasons: Set<String> = ["logo", "rlgo", "rrst", "rsdn", "rest", "shut", "pwrd", "slep"]

    /// System Settings itself, or its Privacy & Security page (a separate process).
    static func isSystemSettings(bundleID: String?, path: String?) -> Bool {
        if let bundleID {
            if bundleID == "com.apple.systempreferences" || bundleID.hasPrefix("com.apple.settings.") { return true }
        }
        guard let path else { return false }
        return path.contains("/System Settings.app/") || path.contains("/System Preferences.app/")
            || path.contains("/SecurityPrivacyExtension.appex/")
    }

    /// Where a person's own quit comes from: the Dock, a logout or restart, the Finder.
    static func isPersonQuit(bundleID: String?, path: String?) -> Bool {
        let people = ["com.apple.dock", "com.apple.loginwindow", "com.apple.finder"]
        if let bundleID, people.contains(bundleID) { return true }
        guard let path else { return false }
        return ["/Dock.app/", "/loginwindow.app/", "/Finder.app/"].contains { path.contains($0) }
    }

    /// Whether Harbor reopens itself after this quit. Always when System Settings
    /// asked. Otherwise only right after Harbor sent the person to a privacy page,
    /// and only when the sender can't be told or is part of macOS itself, never
    /// for the Dock, a logout or another program's quit. Never when the quit
    /// gives a logout, restart, shutdown or sleep reason, whoever sent it. And
    /// never when the live probe says Full Disk Access already works
    /// (`accessProbePasses`, AccessProbe.State.works): nothing then needs a
    /// restart, so a Quit & Reopen the person chose is just a quit, unless
    /// something else still does (`restartStillNeeded`, Screen Recording). Pure.
    ///
    /// This is the only place Harbor decides to reopen itself after a privacy
    /// switch. It runs only for a quit Apple Event macOS sends (Quit & Reopen); a
    /// pane that was opened and a Harbor that came back never reach it.
    static func shouldRelaunch(_ quit: Quit, paneOpenedAt: TimeInterval?, now: TimeInterval,
                               accessProbePasses: Bool = false, restartStillNeeded: Bool = false) -> Bool {
        guard case let .appleEvent(bundleID, path, reason) = quit else { return false }
        if let reason, shutdownReasons.contains(reason) { return false }
        if accessProbePasses && !restartStillNeeded { return false }
        if isSystemSettings(bundleID: bundleID, path: path) { return true }
        // Any other reason from a sender that isn't System Settings: not a plain quit.
        if reason != nil { return false }
        if isPersonQuit(bundleID: bundleID, path: path) { return false }
        guard let paneOpenedAt, paneOpenedAt <= now, now - paneOpenedAt <= paneWindow else { return false }
        let unknown = bundleID == nil && path == nil
        let partOfMacOS = path.map { $0.hasPrefix("/System/Library/") || $0.hasPrefix("/usr/libexec/") } ?? false
        return unknown || partOfMacOS
    }

    /// Before Harbor opens a privacy page or asks for a privacy permission.
    static func notePaneOpened(_ defaults: UserDefaults = .standard, now: Date = Date()) {
        defaults.set(now.timeIntervalSince1970, forKey: paneOpenedKey)
    }

    /// Once a reopen is scheduled: the page has done its part.
    static func clearPaneOpened(_ defaults: UserDefaults = .standard) {
        defaults.removeObject(forKey: paneOpenedKey)
    }

    static func paneOpenedAt(_ defaults: UserDefaults = .standard) -> TimeInterval? {
        defaults.object(forKey: paneOpenedKey) == nil ? nil : defaults.double(forKey: paneOpenedKey)
    }

    // MARK: Harbor's own quit Apple Event handler

    /// The quit Apple Event Harbor's own handler took (AppDelegate.handleQuitAppleEvent).
    /// AppKit's handler checked the responder chain first and dropped the quit without
    /// asking Harbor when the check failed: 2026-11-25 13:39, System Settings' Quit &
    /// Reopen twice ("Failed responder chain validation for terminate: action.
    /// Canceling termination."), then a Force Quit 26 s later. Harbor's handler answers
    /// at once and quits on the next turn of the run loop, when AppKit no longer has
    /// the event as its current one, so `Quit.current()` reads it from here.
    @MainActor static var handledQuitEvent: NSAppleEventDescriptor?

    /// What Harbor's handler does with a quit Apple Event. Pure.
    enum QuitEventAction: Equatable {
        /// Quit the usual way (applicationShouldTerminate, the same safe stop).
        case terminate
        /// A second Quit & Reopen click while the first quit is stopping the engine:
        /// the stop already under way ends Harbor, within its own bound.
        case alreadyQuitting
    }

    static func quitEventAction(shutdownStarted: Bool) -> QuitEventAction {
        shutdownStarted ? .alreadyQuitting : .terminate
    }

    // MARK: Back on the same page

    /// Set when Harbor has started its reopen helper (Quit & Reopen from System
    /// Settings, or the permission page's Reopen button), with the Harbor window
    /// that was up then, so the reopened Harbor shows it again.
    static let reopenAtKey = "harbor.relaunch.reopenAt"
    static let reopenWindowKey = "harbor.relaunch.window"
    /// A reopen comes back within seconds; an older mark is from a quit that
    /// never reopened (the helper gives up after 30 s) and is ignored.
    static let reopenFresh: TimeInterval = 120

    /// The Harbor window surfaces a reopen brings back. The notch's permission page
    /// has its own mark (NotchPermissionPage.noteResume).
    enum ReopenWindow: String, Equatable {
        case settings, window
    }

    static func noteReopenScheduled(_ defaults: UserDefaults = .standard, now: Date = Date()) {
        defaults.set(now.timeIntervalSince1970, forKey: reopenAtKey)
    }

    /// At quit: which Harbor window was up, kept only when a reopen is scheduled.
    static func noteWindowForReopen(_ window: ReopenWindow?, _ defaults: UserDefaults = .standard,
                                    now: Date = Date()) {
        guard let at = defaults.object(forKey: reopenAtKey) as? Double,
              at <= now.timeIntervalSince1970, now.timeIntervalSince1970 - at <= reopenFresh else { return }
        if let window { defaults.set(window.rawValue, forKey: reopenWindowKey) }
        else { defaults.removeObject(forKey: reopenWindowKey) }
    }

    /// At launch, once: the window to show again after a fresh reopen, or nil.
    static func takeReopenWindow(_ defaults: UserDefaults = .standard, now: Date = Date()) -> ReopenWindow? {
        defer {
            defaults.removeObject(forKey: reopenAtKey)
            defaults.removeObject(forKey: reopenWindowKey)
        }
        guard let at = defaults.object(forKey: reopenAtKey) as? Double,
              at <= now.timeIntervalSince1970, now.timeIntervalSince1970 - at <= reopenFresh,
              let raw = defaults.string(forKey: reopenWindowKey) else { return nil }
        return ReopenWindow(rawValue: raw)
    }
}

extension PrivacyRelaunch.Quit {
    /// The quit Apple Event AppKit is handling right now, and who sent it: the
    /// sender's bundle id where Launch Services knows it, and its program's path.
    /// Also the one Harbor's own handler took (PrivacyRelaunch.handledQuitEvent),
    /// once AppKit has moved on from it.
    @MainActor static func current() -> Self {
        let isQuit: (NSAppleEventDescriptor) -> Bool = {
            $0.eventClass == AEEventClass(kCoreEventClass) && $0.eventID == AEEventID(kAEQuitApplication)
        }
        guard let event = [NSAppleEventManager.shared().currentAppleEvent, PrivacyRelaunch.handledQuitEvent]
            .compactMap({ $0 }).first(where: isQuit) else { return .notAppleEvent }
        let why = AEKeyword(kAEQuitReason)
        let reason = (event.attributeDescriptor(forKeyword: why) ?? event.paramDescriptor(forKeyword: why))
            .map { fourCharCode($0.typeCodeValue != 0 ? $0.typeCodeValue : $0.enumCodeValue) }
        let pid = event.attributeDescriptor(forKeyword: AEKeyword(keySenderPIDAttr))?.int32Value ?? 0
        guard pid > 0 else { return .appleEvent(senderBundleID: nil, senderPath: nil, reason: reason) }
        var buffer = [CChar](repeating: 0, count: 4096)
        let path = proc_pidpath(pid, &buffer, UInt32(buffer.count)) > 0 ? String(cString: buffer) : nil
        return .appleEvent(senderBundleID: NSRunningApplication(processIdentifier: pid)?.bundleIdentifier,
                           senderPath: path, reason: reason)
    }

    /// 'logo' from its OSType.
    static func fourCharCode(_ code: OSType) -> String {
        String(bytes: [24, 16, 8, 0].map { UInt8((code >> $0) & 0xFF) }, encoding: .macOSRoman) ?? String(code)
    }
}
