import Darwin
import Foundation

/// How long a launch and a quit take, measured, in the unified log (Console,
/// process Harbor, search "Harbor launch:" or "Harbor quit:"). Each launch milestone is
/// logged once, in milliseconds since macOS started this process, so a Quit &
/// Reopen can be timed end to end from the log alone: the old copy's "Harbor quit:"
/// lines, then the new copy's "Harbor launch:" lines. Only names and numbers.
enum LaunchTimeline {
    enum Milestone: String, CaseIterable {
        /// applicationDidFinishLaunching ran.
        case finishedLaunching = "finished launching"
        /// The notch was on screen: the first run-loop turn after the panel was presented.
        case notchShown = "notch shown"
        /// Harbor's service process was started.
        case serviceStarted = "service started"
        /// The service answered for the first time.
        case serviceReady = "service ready"
        /// Day and the Brief had loaded what the service holds.
        case dayLoaded = "Day and Brief loaded"
    }

    /// When macOS started this process (seconds since 1970), read once from the
    /// kernel; nil if it can't be read.
    static let processStart: TimeInterval? = {
        var info = kinfo_proc()
        var size = MemoryLayout<kinfo_proc>.stride
        var mib: [Int32] = [CTL_KERN, KERN_PROC, KERN_PROC_PID, getpid()]
        guard sysctl(&mib, u_int(mib.count), &info, &size, nil, 0) == 0, size > 0 else { return nil }
        let start = info.kp_proc.p_un.__p_starttime
        return TimeInterval(start.tv_sec) + TimeInterval(start.tv_usec) / 1_000_000
    }()

    @MainActor private static var logged: Set<Milestone> = []

    /// Milliseconds from `start` to `now`, never negative. Pure.
    static func elapsedMilliseconds(since start: TimeInterval, now: TimeInterval) -> Int {
        max(0, Int(((now - start) * 1000).rounded()))
    }

    /// The log line for one milestone. Pure.
    static func line(_ milestone: Milestone, milliseconds: Int) -> String {
        "Harbor launch: \(milestone.rawValue) at +\(milliseconds) ms"
    }

    /// The log line for a finished quit, step by step (each step's own time). Pure.
    static func quitLine(steps: [(String, Int)], total: Int) -> String {
        let parts = steps.map { "\($0.0) \($0.1) ms" }.joined(separator: ", ")
        return "Harbor quit: stopped in \(total) ms" + (parts.isEmpty ? "" : " (\(parts))")
    }

    /// Logs `milestone` the first time it is reached in this launch.
    @MainActor static func mark(_ milestone: Milestone, now: Date = Date()) {
        guard !logged.contains(milestone) else { return }
        logged.insert(milestone)
        let start = processStart ?? now.timeIntervalSince1970
        NSLog("%@", line(milestone, milliseconds: elapsedMilliseconds(since: start, now: now.timeIntervalSince1970)))
    }
}
