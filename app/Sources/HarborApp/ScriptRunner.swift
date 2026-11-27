import AppKit
import Foundation

// Copy/run/open actions for a code block MarkdownView renders. Every action
// here is triggered by an explicit button click the user can see and choose
// not to press — nothing executes automatically. A script is never run from the
// first click: the code block shows the whole script inline and asks (Cancel is
// the default), and only its second button calls `runInTerminalConfirmed`, which
// opens a new, visible Terminal window running the exact command, never silently in
// the background, so what runs is always in plain sight. Terminal gets a private
// `.command` file opened like any document (NSWorkspace), not Apple Events, so macOS
// never asks for Automation access to Terminal; the files delete themselves when the
// window's shell exits.
enum ScriptRunner {
    // Where a code block's language tag says it belongs. `interpreter == nil`
    // for shell/sh/bash/zsh means "run the file directly" (it is written 0700);
    // otherwise the interpreter is prefixed onto the temp file's path.
    enum RunTarget {
        case terminal(interpreter: String?, ext: String)
        case safari
        case pages

        var label: String {
            switch self {
            case .terminal: return "Run in Terminal"
            case .safari: return "Open in Safari"
            case .pages: return "Open in Pages"
            }
        }
        /// True when the block is a script that executes on this Mac. Those are
        /// reachable only through the inline confirmation.
        var runsScript: Bool {
            if case .terminal = self { return true }
            return false
        }
        var icon: String {
            switch self {
            case .terminal: return "terminal"
            case .safari: return "safari"
            case .pages: return "doc.richtext"
            }
        }
    }

    // nil (not `.pages`) means "no run button" — most languages (json/yaml/
    // css/swift/etc.) aren't sensibly "runnable" from a single file, so only
    // Copy shows for those. An UNTAGGED block (b.codeLang == "") is treated
    // as prose/document content bound for Pages, not code.
    static func runTarget(forLang lang: String) -> RunTarget? {
        switch lang.lowercased() {
        case "bash", "sh", "zsh", "shell": return .terminal(interpreter: nil, ext: "sh")
        case "python", "py": return .terminal(interpreter: "python3", ext: "py")
        case "javascript", "js", "node": return .terminal(interpreter: "node", ext: "js")
        case "applescript", "osascript": return .terminal(interpreter: "osascript", ext: "applescript")
        case "html", "htm": return .safari
        case "": return .pages
        default: return nil
        }
    }

    static func copy(_ text: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }

    /// Opens a non-script block (HTML in Safari, prose in Pages). A script target
    /// does nothing here: it only runs through `runInTerminalConfirmed`, after the
    /// code block's own confirmation card.
    static func perform(_ target: RunTarget, code: String) {
        switch target {
        case .terminal:
            return
        case .safari:
            guard let path = writeTemp(code, ext: "html") else { return }
            openWithApp("Safari", path: path)
        case .pages:
            guard let path = writeTemp(code, ext: "txt") else { return }
            openWithApp("Pages", path: path)
        }
    }

    /// Call only from the confirmation card's "Run in Terminal" button.
    static func runInTerminalConfirmed(_ target: RunTarget, code: String) {
        guard case .terminal(let interpreter, let ext) = target else { return }
        runInTerminal(code, interpreter: interpreter, ext: ext)
    }

    /// The one plain sentence the confirmation card shows.
    static func confirmationSentence(for target: RunTarget) -> String {
        var interpreter: String?
        if case .terminal(let name, _) = target { interpreter = name }
        return "It runs with \(interpreterName(interpreter)) on this Mac, with Terminal's permissions, "
            + "so it can read, change or delete your files."
    }

    /// What runs the script, in the person's words.
    static func interpreterName(_ interpreter: String?) -> String {
        guard let interpreter else { return "the shell (sh/zsh)" }
        switch interpreter {
        case "python3": return "Python (python3)"
        case "node": return "JavaScript (node)"
        case "osascript": return "AppleScript (osascript)"
        default: return interpreter
        }
    }

    private static func runInTerminal(_ code: String, interpreter: String?, ext: String) {
        guard let files = writeTerminalFiles(code, interpreter: interpreter, ext: ext),
              let terminal = NSWorkspace.shared.urlForApplication(withBundleIdentifier: "com.apple.Terminal")
        else { return }
        let config = NSWorkspace.OpenConfiguration()
        config.activates = true
        NSWorkspace.shared.open([files.launcher], withApplicationAt: terminal, configuration: config) { _, error in
            // Terminal never got it: nothing will run, so nothing is left behind.
            if error != nil { removeTerminalFiles(files) }
        }
    }

    struct TerminalFiles: Equatable {
        let directory: URL
        let script: URL
        let launcher: URL
    }

    /// The script and the `.command` Terminal opens, in a new folder only this user can
    /// read (0700, files 0700). The launcher runs the script, then deletes the folder when
    /// its shell exits (normally, Ctrl-C or the window closing).
    static func writeTerminalFiles(_ code: String, interpreter: String?, ext: String,
                                   in base: URL = FileManager.default.temporaryDirectory) -> TerminalFiles? {
        let fm = FileManager.default
        let dir = base.appendingPathComponent("harbor-run-\(UUID().uuidString)", isDirectory: true)
        let script = dir.appendingPathComponent("script.\(ext)")
        let launcher = dir.appendingPathComponent("run.command")
        let files = TerminalFiles(directory: dir, script: script, launcher: launcher)
        do {
            try fm.createDirectory(at: dir, withIntermediateDirectories: false,
                                   attributes: [.posixPermissions: 0o700])
            try Data(code.utf8).write(to: script, options: .withoutOverwriting)
            try Data(launcherText(script: script.path, directory: dir.path, interpreter: interpreter).utf8)
                .write(to: launcher, options: .withoutOverwriting)
            try fm.setAttributes([.posixPermissions: 0o700], ofItemAtPath: script.path)
            try fm.setAttributes([.posixPermissions: 0o700], ofItemAtPath: launcher.path)
            return files
        } catch {
            removeTerminalFiles(files)
            return nil
        }
    }

    /// What the `.command` file says. The script runs in the window as typed, so Terminal (not
    /// Harbor) asks for any password.
    static func launcherText(script: String, directory: String, interpreter: String?) -> String {
        let run = interpreter.map { "\($0) \(shellQuote(script))" } ?? shellQuote(script)
        return "#!/bin/bash\n"
            + "HARBOR_RUN_DIR=\(shellQuote(directory))\n"
            + "trap 'rm -rf -- \"$HARBOR_RUN_DIR\"' EXIT\n"
            + "trap 'exit 130' INT HUP TERM\n"
            + "\(run)\n"
    }

    static func removeTerminalFiles(_ files: TerminalFiles) {
        try? FileManager.default.removeItem(at: files.directory)
    }

    private static func openWithApp(_ appName: String, path: String) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/open")
        p.arguments = ["-a", appName, path]
        try? p.run()
    }

    private static func writeTemp(_ content: String, ext: String) -> String? {
        let path = FileManager.default.temporaryDirectory
            .appendingPathComponent("harbor-\(UUID().uuidString).\(ext)")
        do {
            try content.write(to: path, atomically: true, encoding: .utf8)
            return path.path
        } catch {
            return nil
        }
    }

    static func shellQuote(_ s: String) -> String {
        "'" + s.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }

}
