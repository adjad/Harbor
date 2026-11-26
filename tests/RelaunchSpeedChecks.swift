// Contract for a fast quit and relaunch (app/Sources/HarborApp/PrivacyRelaunch.swift,
// app/Sources/HarborApp/LaunchTimeline.swift): Harbor's own quit Apple Event handling,
// the reopened Harbor coming back to the Harbor window that was up, and the timing lines
// in the log. Pure: never quits, launches or opens anything.
import AppKit
import Foundation

@main
struct RelaunchSpeedChecks {
    @MainActor static func main() {
        var count = 0
        func check(_ condition: Bool, _ message: String) {
            count += 1
            if !condition { fputs("FAIL: \(message)\n", stderr); exit(1) }
        }

        // Harbor's own handler: the first quit quits; a second click while stopping
        // leaves the stop under way to end Harbor.
        check(PrivacyRelaunch.quitEventAction(shutdownStarted: false) == .terminate, "a quit Apple Event quits")
        check(PrivacyRelaunch.quitEventAction(shutdownStarted: true) == .alreadyQuitting,
              "a repeat while stopping doesn't start a second stop")

        // The sender is read from the event the handler kept, once AppKit has moved on.
        check(PrivacyRelaunch.Quit.current() == .notAppleEvent, "no quit event: not an Apple Event quit")
        let quit = NSAppleEventDescriptor(eventClass: AEEventClass(kCoreEventClass),
                                          eventID: AEEventID(kAEQuitApplication),
                                          targetDescriptor: nil, returnID: AEReturnID(kAutoGenerateReturnID),
                                          transactionID: AETransactionID(kAnyTransactionID))
        quit.setParam(NSAppleEventDescriptor(enumCode: OSType(0x6C6F_676F)), forKeyword: AEKeyword(kAEQuitReason))  // 'logo'
        PrivacyRelaunch.handledQuitEvent = quit
        if case let .appleEvent(_, _, reason) = PrivacyRelaunch.Quit.current() {
            check(reason == "logo", "the kept event's quit reason is read: \(reason ?? "nil")")
        } else {
            check(false, "the kept quit event counts as an Apple Event quit")
        }
        check(!PrivacyRelaunch.shouldRelaunch(PrivacyRelaunch.Quit.current(), paneOpenedAt: nil, now: 1),
              "a logout read from the kept event never reopens")
        let open = NSAppleEventDescriptor(eventClass: AEEventClass(kCoreEventClass),
                                          eventID: AEEventID(kAEOpenApplication),
                                          targetDescriptor: nil, returnID: AEReturnID(kAutoGenerateReturnID),
                                          transactionID: AETransactionID(kAnyTransactionID))
        PrivacyRelaunch.handledQuitEvent = open
        check(PrivacyRelaunch.Quit.current() == .notAppleEvent, "only a quit event counts")
        PrivacyRelaunch.handledQuitEvent = nil

        // Back on the same page.
        let suite = "harbor.relaunch-speed-checks.\(getpid())"
        guard let defaults = UserDefaults(suiteName: suite) else { fputs("FAIL: defaults\n", stderr); exit(1) }
        defer { defaults.removePersistentDomain(forName: suite) }
        let t0 = Date(timeIntervalSince1970: 1_000_000)
        typealias Window = PrivacyRelaunch.ReopenWindow

        PrivacyRelaunch.noteWindowForReopen(.settings, defaults, now: t0)
        check(PrivacyRelaunch.takeReopenWindow(defaults, now: t0) == nil,
              "a plain quit (no reopen scheduled) never brings a window back")

        PrivacyRelaunch.noteReopenScheduled(defaults, now: t0)
        PrivacyRelaunch.noteWindowForReopen(.settings, defaults, now: t0.addingTimeInterval(1))
        check(PrivacyRelaunch.takeReopenWindow(defaults, now: t0.addingTimeInterval(8)) == .settings,
              "Quit & Reopen with Settings up: Settings comes back")
        check(PrivacyRelaunch.takeReopenWindow(defaults, now: t0.addingTimeInterval(9)) == nil, "once only")

        PrivacyRelaunch.noteReopenScheduled(defaults, now: t0)
        PrivacyRelaunch.noteWindowForReopen(.window, defaults, now: t0)
        check(PrivacyRelaunch.takeReopenWindow(defaults, now: t0.addingTimeInterval(PrivacyRelaunch.reopenFresh + 1)) == nil,
              "a mark older than a reopen takes is ignored (the helper gave up)")
        check(defaults.object(forKey: PrivacyRelaunch.reopenAtKey) == nil
              && defaults.object(forKey: PrivacyRelaunch.reopenWindowKey) == nil, "and cleared")

        PrivacyRelaunch.noteReopenScheduled(defaults, now: t0)
        PrivacyRelaunch.noteWindowForReopen(nil, defaults, now: t0)
        check(PrivacyRelaunch.takeReopenWindow(defaults, now: t0.addingTimeInterval(5)) == nil,
              "no Harbor window up: only the notch (and its own page mark) comes back")

        PrivacyRelaunch.noteReopenScheduled(defaults, now: t0)
        PrivacyRelaunch.noteWindowForReopen(.window, defaults, now: t0)
        check(PrivacyRelaunch.takeReopenWindow(defaults, now: t0.addingTimeInterval(-5)) == nil,
              "a mark from the future (clock change) is ignored")
        check(Window(rawValue: "settings") == .settings && Window(rawValue: "window") == .window
              && Window(rawValue: "chat") == nil, "the stored words")

        // The timing lines.
        check(LaunchTimeline.elapsedMilliseconds(since: 100, now: 101.2506) == 1251, "milliseconds, rounded")
        check(LaunchTimeline.elapsedMilliseconds(since: 100, now: 99) == 0, "never negative")
        check(LaunchTimeline.line(.notchShown, milliseconds: 412) == "Harbor launch: notch shown at +412 ms",
              "a launch line: \(LaunchTimeline.line(.notchShown, milliseconds: 412))")
        check(LaunchTimeline.quitLine(steps: [("engine", 900), ("service", 150)], total: 1100)
              == "Harbor quit: stopped in 1100 ms (engine 900 ms, service 150 ms)", "a quit line")
        check(LaunchTimeline.quitLine(steps: [], total: 40) == "Harbor quit: stopped in 40 ms", "a quit with no steps")
        check(Set(LaunchTimeline.Milestone.allCases.map(\.rawValue)).count == LaunchTimeline.Milestone.allCases.count,
              "distinct milestone names")
        if let start = LaunchTimeline.processStart {
            let now = Date().timeIntervalSince1970
            check(start <= now && now - start < 600, "this process's start time is read from the kernel")
        } else {
            check(false, "the process start time can be read")
        }
        print("\(count) relaunch speed checks passed")
    }
}
