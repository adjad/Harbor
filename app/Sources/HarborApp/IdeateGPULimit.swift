import Foundation
import Darwin

/// macOS's GPU wired-memory limit, and the one Terminal command that raises it for Ideate.
///
/// Harbor's engine sets its memory ceiling to 90% of this Mac's memory (service/engine/manager.py),
/// but the engine can only use what macOS lets the GPU wire: `iogpu.wired_limit_mb`, or macOS's
/// own default (a smaller share of memory) when it is 0. When the limit is below 90%, Ideate's
/// big model has little room to read a prompt, so the tab offers the command.
///
/// Harbor never runs it by itself: the tab shows the whole command, and its Run in Terminal button
/// first swaps the row's buttons for a confirmation (Cancel is the default); only the second
/// press opens it in a Terminal window (ScriptRunner, as a `.command` file: no Apple Events).
/// Terminal asks for the password; Harbor never sees or stores it, and nothing makes the change permanent (it resets when the Mac restarts).
/// Pure apart from `read()`.
struct IdeateGPULimit: Equatable {
    /// The engine's ceiling share (MEMORY_CEILING_SHARE in service/engine/manager.py).
    static let sharePercent: UInt64 = 90

    /// Physical memory in bytes (`hw.memsize`).
    var physicalBytes: UInt64
    /// `iogpu.wired_limit_mb`; 0 means macOS's default, -1 that it couldn't be read.
    var currentMB: Int

    /// 90% of memory in MB, rounded down (22118 on a 24 GB Mac).
    var neededMB: Int { Int(physicalBytes / 100 * Self.sharePercent / 1_048_576) }

    /// The limit is known and below what the engine's ceiling needs. An unreadable limit or
    /// memory size shows nothing.
    var isLow: Bool { physicalBytes > 0 && currentMB >= 0 && currentMB < neededMB }

    var command: String { "sudo sysctl iogpu.wired_limit_mb=\(neededMB)" }

    /// The command as a bash block, for answers that quote it.
    var codeBlock: String { "```bash\n\(command)\n```" }

    static let title = "Ideate needs more memory for the GPU"
    static let detail = "Run one command in Terminal to allow it. macOS asks for your password there, "
        + "and Harbor never sees it. It resets when you restart the Mac."
    static let skipNote = "Skip, and Ideate keeps to small steps."
    static let confirmNote = "It opens in a new Terminal window, which asks for your Mac password. "
        + "Harbor never sees it."
    static let raised = "GPU memory raised for Ideate. It resets when you restart the Mac."

    /// What this Mac says now. Reading needs no password.
    static func read() -> IdeateGPULimit {
        // An int today; an 8-byte buffer also reads it if it grows (little-endian, zeroed).
        var mb: Int64 = 0
        var size = MemoryLayout<Int64>.size
        let ok = sysctlbyname("iogpu.wired_limit_mb", &mb, &size, nil, 0) == 0
        return IdeateGPULimit(physicalBytes: ProcessInfo.processInfo.physicalMemory, currentMB: ok ? Int(mb) : -1)
    }
}
