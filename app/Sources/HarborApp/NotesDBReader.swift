import Foundation
import SQLite3
import Compression

// Reads Notes straight from its own on-disk store
// (~/Library/Group Containers/group.com.apple.notes/NoteStore.sqlite) instead
// of scripting Notes.app — so syncing notes never has to launch the app. Same
// Full Disk Access grant BrowserHistoryReader/MessagesReader already use.
//
// Verified against the real database on this machine before writing any of
// this (dumped sqlite_master + sampled real rows):
//   - Z_ENT for the "this row is a note" entity is looked up by name
//     ('ICNote') rather than hardcoded — Z_ENT numbering is assigned by
//     Core Data's model and isn't guaranteed stable across app/OS versions.
//   - A note's body lives in ZICNOTEDATA.ZDATA, one row per note (joined via
//     ZICCLOUDSYNCINGOBJECT.ZNOTEDATA), NOT inline on the note row itself.
//   - ZDATA is usually gzip-compressed (magic bytes 1F 8B) but NOT always —
//     small notes on this machine (~160 bytes) were stored as raw,
//     uncompressed protobuf. Both cases must be handled.
//
// From Notes' published store layout (the checks use invented fixtures in
// that shape, never a real store):
//   - The payload is a protobuf blob: NoteStoreProto, whose
//     field 2 is the document, whose field 3 is the note, whose field 2 is
//     the note's text (title line included). ProtobufTextExtractor follows
//     exactly that path. It once took the longest string anywhere in the
//     tree instead, which dropped 1-3 character notes and let a UUID or a
//     style string win over a short note's text. That method is kept only as
//     a fallback when the path finds no text, so a store laid out otherwise
//     still yields its notes.
//   - Recently Deleted is a folder row whose ZFOLDERTYPE is 1 (its title is
//     localized, so the type decides, not the name). A trashed note keeps
//     ZMARKEDFORDELETION at 0 and only moves to that folder, so the folder
//     type is checked, along with the parent of a folder deleted whole.
//   - A table is an attachment row (ZTYPEUTI 'com.apple.notes.table', ZNOTE
//     = the note) whose ZMERGEABLEDATA1 holds its cells, each a Note message
//     like the body's. The cells are added after the body text.
final class NotesDBReader {
    private let noteLimit = 500   // matches NotesReader's AppleScript `lim`
    private let path: String

    /// Why the last readNotes() returned nil, for NotesReader's diagnostics.
    /// `kind` is the reason_kind the Brief keys its button on ("" for none).
    private(set) var lastFailure: (kind: String, reason: String)?

    // An explicit path lets checks use fixtures, never the person's Notes.
    init(storePath: String? = nil) {
        path = storePath ?? (NSHomeDirectory() as NSString)
            .appendingPathComponent("Library/Group Containers/group.com.apple.notes/NoteStore.sqlite")
    }

    /// Same FS/RS-delimited raw format as NotesReader's AppleScript path
    /// ("epochSecs FS name FS folder FS plaintext RS"), so the backend's
    /// existing parser needs no changes regardless of which reader produced
    /// a given sync. Returns nil, with lastFailure set, when the store can't
    /// be opened, its tables aren't the ones this reader knows, or the read
    /// stops before its last row; a store it can't read is never reported as
    /// an empty or shorter one.
    func readNotes() -> String? {
        lastFailure = nil
        var db: OpaquePointer?
        guard sqlite3_open_v2(path, &db, SQLITE_OPEN_READONLY, nil) == SQLITE_OK else {
            sqlite3_close(db)
            lastFailure = Self.failure(openErrno: Self.openErrno(path))
            return nil
        }
        defer { sqlite3_close(db) }
        // Notes may be writing while this reads: wait briefly for its lock.
        sqlite3_busy_timeout(db, 2000)

        guard let noteEnt = entityID(db, name: "ICNote") else {
            lastFailure = Self.unreadable(path)
            return nil
        }

        let columns = columnNames(db, table: "ZICCLOUDSYNCINGOBJECT")
        // Recently Deleted by folder type where the store has it; an older
        // store without the column falls back to the English folder name.
        var trash = ""
        if columns.contains("ZFOLDERTYPE") {
            trash = "\n  AND (f.ZFOLDERTYPE IS NULL OR f.ZFOLDERTYPE != 1)"
            if columns.contains("ZPARENT") {
                trash += "\n  AND (p.ZFOLDERTYPE IS NULL OR p.ZFOLDERTYPE != 1)"
            }
        } else {
            trash = "\n  AND (f.ZTITLE2 IS NULL OR f.ZTITLE2 != 'Recently Deleted')"
        }
        let parentJoin = columns.contains("ZFOLDERTYPE") && columns.contains("ZPARENT")
            ? "\nLEFT JOIN ZICCLOUDSYNCINGOBJECT p ON p.Z_PK = f.ZPARENT" : ""
        // Newest first, so the 500 kept are the most recent. A note with no
        // date sorts last but is still read.
        let sql = """
        SELECT n.ZTITLE1, f.ZTITLE2, n.ZMODIFICATIONDATE1, d.ZDATA, n.Z_PK
        FROM ZICCLOUDSYNCINGOBJECT n
        JOIN ZICNOTEDATA d ON d.ZNOTE = n.Z_PK
        LEFT JOIN ZICCLOUDSYNCINGOBJECT f ON f.Z_PK = n.ZFOLDER\(parentJoin)
        WHERE n.Z_ENT = \(noteEnt)
          AND (n.ZMARKEDFORDELETION IS NULL OR n.ZMARKEDFORDELETION = 0)\(trash)
        ORDER BY COALESCE(n.ZMODIFICATIONDATE1, 0) DESC, n.Z_PK DESC
        LIMIT \(noteLimit)
        """
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else {
            lastFailure = Self.unreadable(path)
            return nil
        }
        defer { sqlite3_finalize(stmt) }
        let tables = tableTexts(db, columns: columns)

        var out = ""
        var step = sqlite3_step(stmt)
        while step == SQLITE_ROW {
            // Runs on every way out of this pass, the skips below included.
            defer { step = sqlite3_step(stmt) }
            let title = Self.carrierSafe(text(stmt, 0) ?? "(untitled)")
            let folder = Self.carrierSafe(text(stmt, 1) ?? "")
            // Core Data epoch (2001-01-01) — same constant used across this
            // codebase (MessagesReader, BrowserHistoryReader) for every other
            // Apple store EXCEPT Mail's Envelope Index, which is plain Unix
            // epoch instead (confirmed separately — see MailDBReader). A note
            // without a usable date is kept with 0, which the backend shows
            // as an unknown date.
            let modified = sqlite3_column_double(stmt, 2)
            let epoch: Int64 = modified > 0 ? Int64(modified + 978307200) : 0

            guard let blob = sqlite3_column_blob(stmt, 3) else { continue }
            let len = Int(sqlite3_column_bytes(stmt, 3))
            guard len > 0 else { continue }
            let raw = Data(bytes: blob, count: len)
            let payload = gunzipIfNeeded(raw) ?? raw
            var body = ProtobufTextExtractor.bodyText(in: payload)
                .replacingOccurrences(of: "\u{FFFC}", with: "")   // attachment markers
            for cells in tables[sqlite3_column_int64(stmt, 4)] ?? [] {
                body += (body.hasSuffix("\n") || body.isEmpty ? "" : "\n") + cells.joined(separator: "\n")
            }
            body = Self.carrierSafe(body)
            guard !body.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { continue }

            out += epoch.description + FS + title + FS + folder + FS + body + RS
        }
        // SQLITE_BUSY, SQLITE_IOERR and SQLITE_CORRUPT are failures, not an
        // empty or shorter Notes.
        guard step == SQLITE_DONE else {
            lastFailure = Self.interrupted(path)
            return nil
        }
        return out
    }

    private let FS = "\u{01}", RS = "\u{02}"

    /// The carrier's own delimiters can't appear inside a field.
    static func carrierSafe(_ s: String) -> String {
        s.replacingOccurrences(of: "\u{01}", with: "").replacingOccurrences(of: "\u{02}", with: "")
    }

    /// Each note's table cells, keyed by the note's Z_PK, in store order.
    /// Best effort: a store without table columns, or a table that can't be
    /// read, adds nothing and never fails the read of the notes themselves.
    private func tableTexts(_ db: OpaquePointer?, columns: Set<String>) -> [Int64: [[String]]] {
        let dataColumn = columns.contains("ZMERGEABLEDATA1") ? "ZMERGEABLEDATA1"
            : columns.contains("ZMERGEABLEDATA") ? "ZMERGEABLEDATA" : nil
        guard let dataColumn, columns.contains("ZTYPEUTI"), columns.contains("ZNOTE") else { return [:] }
        let sql = """
        SELECT ZNOTE, \(dataColumn) FROM ZICCLOUDSYNCINGOBJECT
        WHERE ZTYPEUTI = 'com.apple.notes.table' AND ZNOTE IS NOT NULL
          AND (ZMARKEDFORDELETION IS NULL OR ZMARKEDFORDELETION = 0)
        ORDER BY Z_PK
        """
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [:] }
        defer { sqlite3_finalize(stmt) }
        var out: [Int64: [[String]]] = [:]
        while sqlite3_step(stmt) == SQLITE_ROW {
            guard let blob = sqlite3_column_blob(stmt, 1) else { continue }
            let len = Int(sqlite3_column_bytes(stmt, 1))
            guard len > 0 else { continue }
            let raw = Data(bytes: blob, count: len)
            let cells = ProtobufTextExtractor.tableCellTexts(in: gunzipIfNeeded(raw) ?? raw)
            if !cells.isEmpty { out[sqlite3_column_int64(stmt, 0), default: []].append(cells) }
        }
        return out
    }

    private func columnNames(_ db: OpaquePointer?, table: String) -> Set<String> {
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, "PRAGMA table_info(\(table))", -1, &stmt, nil) == SQLITE_OK else { return [] }
        defer { sqlite3_finalize(stmt) }
        var names = Set<String>()
        while sqlite3_step(stmt) == SQLITE_ROW {
            if let name = text(stmt, 1) { names.insert(name.uppercased()) }
        }
        return names
    }

    /// Why the store wouldn't open, from the error itself.
    ///
    /// - IMPORTANT: Full Disk Access only when macOS refused the file (EPERM,
    ///   its privacy denial), as in MessagesReadFailure and MailDBReader. A
    ///   missing, locked or changed store keeps a plain reason, so nobody is
    ///   sent to a setting that won't help.
    static func failure(openErrno: Int32) -> (kind: String, reason: String) {
        switch openErrno {
        case EPERM:
            return ("full_disk_access", "Harbor reads Notes while Notes is open. Open Notes, or give Harbor "
                    + "Full Disk Access to read it when Notes is closed.")
        case ENOENT:
            return ("", "Harbor didn't find saved notes on this Mac. Open Notes, and Harbor will read it from there.")
        default:
            return ("", "Notes' saved data couldn't be read just now. Open Notes, and Harbor will read it from there.")
        }
    }

    /// The store opened but its tables can't be read: still EPERM if macOS
    /// refuses the file itself, otherwise a store this reader doesn't know.
    private static func unreadable(_ path: String) -> (kind: String, reason: String) {
        let code = openErrno(path)
        if code == EPERM { return failure(openErrno: code) }
        return ("", "Notes' saved data is in a form Harbor can't read. Open Notes, and Harbor will read it from there.")
    }

    /// The read began and then stopped early (busy, I/O error, damaged
    /// pages): still EPERM if macOS refuses the file itself, otherwise the
    /// plain "just now" reason, since the next read may well succeed.
    private static func interrupted(_ path: String) -> (kind: String, reason: String) {
        failure(openErrno: openErrno(path) == EPERM ? EPERM : 0)
    }

    /// SQLite's own errno after a failed open isn't reliable, so the file is
    /// opened once more read-only: that errno is the real reason (0 if it opens).
    static func openErrno(_ path: String) -> Int32 {
        let fd = open(path, O_RDONLY)
        if fd >= 0 { close(fd); return 0 }
        return errno
    }

    private func entityID(_ db: OpaquePointer?, name: String) -> Int64? {
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, "SELECT Z_ENT FROM Z_PRIMARYKEY WHERE Z_NAME = ?", -1, &stmt, nil) == SQLITE_OK else { return nil }
        defer { sqlite3_finalize(stmt) }
        sqlite3_bind_text(stmt, 1, name, -1, unsafeBitCast(-1, to: sqlite3_destructor_type.self))
        guard sqlite3_step(stmt) == SQLITE_ROW else { return nil }
        return sqlite3_column_int64(stmt, 0)
    }

    private func text(_ stmt: OpaquePointer?, _ idx: Int32) -> String? {
        guard let c = sqlite3_column_text(stmt, idx) else { return nil }
        return String(cString: c)
    }

    /// Decompresses a gzip-wrapped blob, or returns nil if `data` isn't
    /// gzip (no 1F 8B magic) — callers fall back to treating it as raw
    /// protobuf directly, which is what small, uncompressed notes actually
    /// are on this machine.
    private func gunzipIfNeeded(_ data: Data) -> Data? {
        guard data.count > 18, data[data.startIndex] == 0x1f, data[data.startIndex + 1] == 0x8b else { return nil }
        var offset = data.startIndex + 10
        let flags = data[data.startIndex + 3]
        if flags & 0x04 != 0, offset + 2 <= data.endIndex {   // FEXTRA
            let xlen = Int(data[offset]) | (Int(data[offset + 1]) << 8)
            offset += 2 + xlen
        }
        if flags & 0x08 != 0 {   // FNAME
            while offset < data.endIndex, data[offset] != 0 { offset += 1 }
            offset += 1
        }
        if flags & 0x10 != 0 {   // FCOMMENT
            while offset < data.endIndex, data[offset] != 0 { offset += 1 }
            offset += 1
        }
        if flags & 0x02 != 0 { offset += 2 }   // FHCRC
        guard offset < data.endIndex - 8 else { return nil }
        let deflate = data.subdata(in: offset..<(data.endIndex - 8))

        // libcompression's COMPRESSION_ZLIB algorithm decodes raw DEFLATE
        // (no zlib/gzip framing) despite the name — that's exactly what's
        // left once the gzip header/trailer above are stripped.
        var capacity = max(deflate.count * 8, 4096)
        while capacity <= 32 * 1024 * 1024 {
            let decoded = deflate.withUnsafeBytes { srcPtr -> Data? in
                guard let src = srcPtr.bindMemory(to: UInt8.self).baseAddress else { return nil }
                var dst = [UInt8](repeating: 0, count: capacity)
                let n = compression_decode_buffer(&dst, capacity, src, deflate.count, nil, COMPRESSION_ZLIB)
                guard n > 0 else { return nil }
                if n == capacity { return nil }   // likely truncated — grow and retry
                return Data(dst[0..<n])
            }
            if let decoded { return decoded }
            capacity *= 4
        }
        return nil
    }
}

/// Reads the note's text by its field path (see NotesDBReader's header):
/// NoteStoreProto.document (2) -> Document.note (3) -> Note.note_text (2).
/// Following the path, rather than guessing at the longest string, keeps a
/// one-character note and never takes a UUID or style string instead.
enum ProtobufTextExtractor {
    /// The note's text, or nil when the payload isn't a note in that shape.
    static func noteText(in data: Data) -> String? {
        guard let document = message(data, field: 2), let note = message(document, field: 3) else { return nil }
        return string(note, field: 2)
    }

    /// What the reader keeps as the body: the note's text by its field path,
    /// or, when that path finds no text, the longest plausible string in the
    /// tree (the reader's old method). The field path isn't checked against a
    /// real store here, so a wrong path must not make notes disappear.
    static func bodyText(in data: Data) -> String {
        if let text = noteText(in: data), !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return text
        }
        return longestString(in: data) ?? ""
    }

    /// The old heuristic: the longest decodable UTF-8 string of 4+ characters
    /// with no control bytes, anywhere in the message tree. Only a fallback.
    static func longestString(in data: Data) -> String? {
        var best: String?
        walk(data, into: &best)
        return best
    }

    private static func walk(_ data: Data, into best: inout String?) {
        var i = data.startIndex
        while i < data.endIndex {
            guard let (tag, next) = readVarint(data, i) else { return }
            i = next
            switch tag & 0x7 {
            case 0:
                guard let (_, n) = readVarint(data, i) else { return }
                i = n
            case 1:
                guard data.endIndex - i >= 8 else { return }
                i += 8
            case 5:
                guard data.endIndex - i >= 4 else { return }
                i += 4
            case 2:
                guard let (len, n) = readVarint(data, i) else { return }
                // UInt64 compare first: Int(len) would trap past Int.max.
                guard len <= UInt64(data.endIndex - n) else { return }
                let end = n + Int(len)
                let chunk = data.subdata(in: n..<end)
                if let s = plausibleText(chunk), s.count > (best?.count ?? 0) { best = s }
                // A string can also parse as a (nonsensical) message: try both.
                walk(chunk, into: &best)
                i = end
            default:
                return
            }
        }
    }

    private static func plausibleText(_ chunk: Data) -> String? {
        guard chunk.count >= 4, let s = String(data: chunk, encoding: .utf8) else { return nil }
        for scalar in s.unicodeScalars where scalar.value < 0x20 && scalar != "\n" && scalar != "\t" {
            return nil
        }
        return s
    }

    /// A table attachment's cell texts, in stored order: MergableDataProto
    /// .mergable_data_object (2) -> .mergeable_data_object_data (3) ->
    /// each .mergeable_data_object_entry (3) -> .note (10) -> .note_text (2).
    /// Empty cells are left out; an unreadable table gives no cells.
    static func tableCellTexts(in data: Data) -> [String] {
        guard let object = message(data, field: 2), let objectData = message(object, field: 3),
              let entries = fields(objectData)?.filter({ $0.number == 3 && $0.wireType == 2 }) else { return [] }
        var cells: [String] = []
        for entry in entries {
            guard let note = message(entry.value, field: 10), let text = string(note, field: 2) else { continue }
            let cell = text.replacingOccurrences(of: "\u{FFFC}", with: "")
                .trimmingCharacters(in: .whitespacesAndNewlines)
            if !cell.isEmpty { cells.append(cell) }
        }
        return cells
    }

    /// An embedded message field. Repeated occurrences are joined, which is
    /// how protobuf merges them. Nil when the field is absent or `data`
    /// doesn't parse as a message.
    private static func message(_ data: Data, field: UInt64) -> Data? {
        guard let all = fields(data) else { return nil }
        let parts = all.filter { $0.number == field && $0.wireType == 2 }
        guard !parts.isEmpty else { return nil }
        return parts.reduce(into: Data()) { $0.append($1.value) }
    }

    /// A string field (the last occurrence wins, as in protobuf), or nil
    /// when it's absent or not valid UTF-8.
    private static func string(_ data: Data, field: UInt64) -> String? {
        guard let last = fields(data)?.last(where: { $0.number == field && $0.wireType == 2 }) else { return nil }
        return String(data: last.value, encoding: .utf8)
    }

    /// Every top-level field of a message, or nil if it doesn't parse to the
    /// end. Varint and fixed-width values are skipped (their value is empty).
    private static func fields(_ data: Data) -> [(number: UInt64, wireType: UInt64, value: Data)]? {
        var out: [(number: UInt64, wireType: UInt64, value: Data)] = []
        var i = data.startIndex
        while i < data.endIndex {
            guard let (tag, next) = readVarint(data, i) else { return nil }
            i = next
            let number = tag >> 3, wireType = tag & 0x7
            guard number > 0 else { return nil }
            switch wireType {
            case 0:
                guard let (_, n) = readVarint(data, i) else { return nil }
                i = n
                out.append((number, wireType, Data()))
            case 1:
                guard data.endIndex - i >= 8 else { return nil }
                i += 8
                out.append((number, wireType, Data()))
            case 5:
                guard data.endIndex - i >= 4 else { return nil }
                i += 4
                out.append((number, wireType, Data()))
            case 2:
                guard let (len, n) = readVarint(data, i) else { return nil }
                // Compared as UInt64 before converting: a damaged length can
                // be past Int.max, and Int(len) would trap on it.
                guard len <= UInt64(data.endIndex - n) else { return nil }
                let end = n + Int(len)
                out.append((number, wireType, data.subdata(in: n..<end)))
                i = end
            default:
                return nil   // wire type 3/4 (deprecated groups) or invalid
            }
        }
        return out
    }

    private static func readVarint(_ data: Data, _ start: Data.Index) -> (UInt64, Data.Index)? {
        var result: UInt64 = 0
        var shift: UInt64 = 0
        var i = start
        while i < data.endIndex {
            let byte = data[i]
            result |= UInt64(byte & 0x7f) << shift
            i += 1
            if byte & 0x80 == 0 { return (result, i) }
            shift += 7
            if shift >= 64 { return nil }
        }
        return nil
    }
}

/// The AppleScript read's order (NotesReader): `notes` comes back in no
/// promised order, so a first script lists every note's id and modification
/// date, and the second reads them newest first, skipping Recently Deleted.
/// Kept here, free of AppKit, so the checks can run it.
enum NotesScriptRead {
    static let limit = 500
    /// Ids handed to the second script: the limit plus room for notes it
    /// skips (Recently Deleted, unreadable ones), so the script stays small.
    static let candidateLimit = 2000

    static let listScript = """
    tell application "Notes"
        return {id of every note, modification date of every note}
    end tell
    """

    /// Ids newest first; a note without a date goes last, never dropped.
    /// Ties keep Notes' own order.
    static func newestFirst(ids: [String], dates: [Date?]) -> [String] {
        let pairs = Array(zip(ids, dates).enumerated())
        return pairs.sorted { a, b in
            let da = a.element.1 ?? .distantPast, db = b.element.1 ?? .distantPast
            return da != db ? da > db : a.offset < b.offset
        }.map { $0.element.0 }.prefix(candidateLimit).map { $0 }
    }

    /// The second script: reads the given notes in order and stops after
    /// `limit`. Recently Deleted is matched by its English name, as the write
    /// tools do: AppleScript doesn't expose the folder's type.
    static func readScript(ids: [String]) -> String {
        let list = ids.map { "\"" + $0.replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "\"", with: "\\\"") + "\"" }.joined(separator: ", ")
        return """
        tell application "Notes"
            set refDate to current date
            set year of refDate to 2001
            set month of refDate to 1
            set day of refDate to 1
            set time of refDate to 0
            set FS to ASCII character 1
            set RS to ASCII character 2
            set output to ""
            set kept to 0
            set wanted to {\(list)}
            repeat with nid in wanted
                if kept is \(limit) then exit repeat
                try
                    set nt to note id (nid as text)
                    set folderName to ""
                    try
                        set folderName to name of container of nt
                    end try
                    if folderName is not "Recently Deleted" then
                        set epochSecs to ((modification date of nt) - refDate) + 978307200
                        set output to output & epochSecs & FS & (name of nt) & FS & folderName & FS & (plaintext of nt) & RS
                        set kept to kept + 1
                    end if
                end try
            end repeat
            return output
        end tell
        """
    }
}
