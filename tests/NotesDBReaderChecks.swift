// Why closed Notes can't be read, and what is read from a store that can be,
// from synthetic fixtures only: never the person's NoteStore. Compile with
// NotesDBReader.swift (and -lsqlite3).
import Foundation
import SQLite3

@main struct NotesDBReaderChecks {
    static func check(_ ok: Bool, _ message: String) { precondition(ok, message) }

    static func main() throws {
        typealias R = NotesDBReader
        // Full Disk Access only for macOS's privacy denial.
        let denied = R.failure(openErrno: EPERM)
        check(denied.kind == "full_disk_access" && denied.reason.contains("Full Disk Access"), "EPERM")
        for code in [ENOENT, EACCES, EBUSY, EIO, 0] {
            let other = R.failure(openErrno: code)
            check(other.kind == "" && !other.reason.contains("Full Disk Access"),
                  "errno \(code) keeps a plain reason")
        }

        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("harbor-notes-db-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        // Missing store: plain reason, not Full Disk Access.
        let missing = R(storePath: root.appendingPathComponent("NoteStore.sqlite").path)
        check(missing.readNotes() == nil, "a missing store can't be read")
        check(missing.lastFailure?.kind == "" && missing.lastFailure?.reason.contains("didn't find") == true,
              "a missing store says so plainly")

        // A store whose tables changed: unreadable, never an empty success.
        let changedPath = root.appendingPathComponent("Changed.sqlite").path
        var db: OpaquePointer?
        check(sqlite3_open(changedPath, &db) == SQLITE_OK, "fixture open")
        check(sqlite3_exec(db, "CREATE TABLE SOMETHING_ELSE (x INTEGER)", nil, nil, nil) == SQLITE_OK, "fixture")
        sqlite3_close(db)
        let changed = R(storePath: changedPath)
        check(changed.readNotes() == nil, "an unknown schema is not an empty Notes")
        check(changed.lastFailure?.kind == "" && changed.lastFailure?.reason.contains("can't read") == true,
              "an unknown schema keeps a plain reason")

        // A store with the known entity but no note tables: also unreadable.
        let partialPath = root.appendingPathComponent("Partial.sqlite").path
        check(sqlite3_open(partialPath, &db) == SQLITE_OK, "fixture open")
        check(sqlite3_exec(db, "CREATE TABLE Z_PRIMARYKEY (Z_ENT INTEGER, Z_NAME TEXT); "
                           + "INSERT INTO Z_PRIMARYKEY VALUES (5, 'ICNote')", nil, nil, nil) == SQLITE_OK, "fixture")
        sqlite3_close(db)
        let partial = R(storePath: partialPath)
        check(partial.readNotes() == nil && partial.lastFailure?.kind == "", "missing note tables")

        // A readable, empty store is a real empty result with no failure.
        let noteTables = """
            CREATE TABLE Z_PRIMARYKEY (Z_ENT INTEGER, Z_NAME TEXT);
            INSERT INTO Z_PRIMARYKEY VALUES (5, 'ICNote');
            CREATE TABLE ZICCLOUDSYNCINGOBJECT (Z_PK INTEGER, Z_ENT INTEGER, ZTITLE1 TEXT, ZTITLE2 TEXT,
                ZMODIFICATIONDATE1 REAL, ZFOLDER INTEGER, ZMARKEDFORDELETION INTEGER);
            CREATE TABLE ZICNOTEDATA (ZNOTE INTEGER, ZDATA BLOB);

            """
        let emptyPath = root.appendingPathComponent("Empty.sqlite").path
        check(sqlite3_open(emptyPath, &db) == SQLITE_OK, "fixture open")
        check(sqlite3_exec(db, noteTables, nil, nil, nil) == SQLITE_OK, "fixture")
        sqlite3_close(db)
        let empty = R(storePath: emptyPath)
        check(empty.readNotes() == "" && empty.lastFailure == nil, "an empty readable store is empty")
        // A later failure on the same reader replaces nothing stale.
        check(missing.readNotes() == nil && missing.lastFailure != nil, "each read sets its own failure")

        // Hand-written protobuf in Notes' shape, all invented: NoteStoreProto
        // field 2 (document) -> field 3 (note) -> field 2 (note_text).
        typealias P = ProtobufTextExtractor
        func varint(_ v: Int) -> Data {
            var v = v, out = Data()
            repeat {
                var byte = UInt8(v & 0x7f); v >>= 7
                if v != 0 { byte |= 0x80 }
                out.append(byte)
            } while v != 0
            return out
        }
        func ld(_ field: Int, _ payload: Data) -> Data { varint(field << 3 | 2) + varint(payload.count) + payload }
        func ld(_ field: Int, _ text: String) -> Data { ld(field, Data(text.utf8)) }
        func num(_ field: Int, _ value: Int) -> Data { varint(field << 3) + varint(value) }
        // A note: version, then the note with its text and a style run.
        func store(_ body: String, noteExtras: Data = Data(), docExtras: Data = Data()) -> Data {
            ld(2, num(2, 1) + docExtras + ld(3, ld(2, body) + noteExtras))
        }

        // The smallest note: "A" in every layer, written out byte by byte.
        let oneLetter = Data([0x12, 0x07, 0x10, 0x01, 0x1A, 0x03, 0x12, 0x01, 0x41])
        check(P.noteText(in: oneLetter) == "A", "a one-letter note is read")
        for short in ["A", "Hi", "ok!"] {
            check(P.noteText(in: store(short)) == short, "a short note (\(short.count) chars) survives")
        }
        // Longer strings elsewhere in the tree never replace the note's text:
        // a UUID beside the note, a style run's font name, an attachment id.
        let uuid = "7D1B5F0C-3A2E-4C8B-9F11-2B6E8A4D0C55"
        let style = ld(5, num(1, 2) + ld(2, ld(1, "HelveticaNeue-MediumItalicLongFontName")))
        let attachment = ld(5, num(1, 1) + ld(12, ld(1, uuid) + ld(2, "com.apple.notes.table")))
        let crowded = store("Gym", noteExtras: style + attachment, docExtras: ld(1, uuid))
        check(P.noteText(in: crowded) == "Gym", "the note's text wins over UUIDs and metadata")
        for body in ["Hi\n你好吗?", "Hi\n고마워!", "b日本語a is how this note starts, and it goes on."] {
            check(P.noteText(in: store(body)) == body, "a body with CJK text is read whole")
        }
        check(P.noteText(in: Data("b日本語a".utf8)) == nil, "bare text is not a note")
        check(P.noteText(in: ld(2, "Call the plumber")) == nil, "a store without the note path has no text")
        var hugeLength: [UInt8] = [0x12]
        hugeLength += [UInt8](repeating: 0xFF, count: 9)
        hugeLength.append(0x01)
        check(P.noteText(in: Data(hugeLength)) == nil, "a length past Int.max is not followed")
        check(P.noteText(in: store("Call the plumber on Monday")) == "Call the plumber on Monday",
              "plain text is read as before")

        // A table: MergableDataProto 2 -> object 3 -> entries 3 -> note 10 -> text 2.
        func cell(_ text: String) -> Data { ld(3, ld(10, ld(2, text))) }
        let rowsEntry = ld(3, ld(6, ld(1, num(1, 0))))   // a dictionary entry, no text
        let table = ld(2, num(2, 1) + ld(3, rowsEntry + cell("Monday") + cell("Swim") + cell(" ")
                                           + cell("Tuesday") + cell("Run") + ld(4, "crRows")))
        check(P.tableCellTexts(in: table) == ["Monday", "Swim", "Tuesday", "Run"],
              "a table's cell texts are read in order, empty ones left out")
        check(P.tableCellTexts(in: Data(hugeLength)).isEmpty, "an unreadable table has no cells")

        // Notes' AppleScript read: newest first, undated last, never dropped.
        let day = 86_400.0
        let ordered = NotesScriptRead.newestFirst(
            ids: ["old", "undated", "new", "mid", "tie"],
            dates: [Date(timeIntervalSince1970: 1 * day), nil, Date(timeIntervalSince1970: 9 * day),
                    Date(timeIntervalSince1970: 5 * day), Date(timeIntervalSince1970: 5 * day)])
        check(ordered == ["new", "mid", "tie", "old", "undated"], "script reads take the newest notes first")
        let script = NotesScriptRead.readScript(ids: ["x-coredata://A/ICNote/p1", "say \"hi\""])
        check(script.contains("{\"x-coredata://A/ICNote/p1\", \"say \\\"hi\\\"\"}"), "ids are quoted for AppleScript")
        check(script.contains("is not \"Recently Deleted\"") && script.contains("if kept is 500 then exit repeat"),
              "the script skips Recently Deleted and stops at 500")

        // Current layout. Rows: two folders (one is the trash, under a
        // localized name, so its type decides), a folder deleted whole inside
        // the trash, and notes to return or skip. Rows are inserted out of
        // date order; the read must return them newest first.
        func hex(_ data: Data) -> String { data.map { String(format: "%02x", $0) }.joined() }
        let currentTables = """
            CREATE TABLE Z_PRIMARYKEY (Z_ENT INTEGER, Z_NAME TEXT);
            INSERT INTO Z_PRIMARYKEY VALUES (5, 'ICNote');
            CREATE TABLE ZICCLOUDSYNCINGOBJECT (Z_PK INTEGER, Z_ENT INTEGER, ZTITLE1 TEXT, ZTITLE2 TEXT,
                ZMODIFICATIONDATE1 REAL, ZFOLDER INTEGER, ZMARKEDFORDELETION INTEGER, ZFOLDERTYPE INTEGER,
                ZPARENT INTEGER, ZNOTE INTEGER, ZTYPEUTI TEXT, ZMERGEABLEDATA1 BLOB);
            CREATE TABLE ZICNOTEDATA (ZNOTE INTEGER, ZDATA BLOB);

            """
        let greeting = "Greeting\nHi\n你好吗?", plumber = "Plumber\nCall the plumber on Monday"
        let cols = "(Z_PK, Z_ENT, ZTITLE1, ZTITLE2, ZMODIFICATIONDATE1, ZFOLDER, ZMARKEDFORDELETION, ZFOLDERTYPE, ZPARENT)"
        let currentRows = """
            INSERT INTO ZICCLOUDSYNCINGOBJECT \(cols) VALUES (10, 3, NULL, 'Home', NULL, NULL, 0, 0, NULL);
            INSERT INTO ZICCLOUDSYNCINGOBJECT \(cols) VALUES (11, 3, NULL, 'Zuletzt gelöscht', NULL, NULL, 0, 1, NULL);
            INSERT INTO ZICCLOUDSYNCINGOBJECT \(cols) VALUES (12, 3, NULL, 'Old trip', NULL, NULL, 0, 0, 11);
            INSERT INTO ZICCLOUDSYNCINGOBJECT \(cols) VALUES (3, 5, 'Plumber', NULL, 600000000, NULL, 0, NULL, NULL);
            INSERT INTO ZICCLOUDSYNCINGOBJECT \(cols) VALUES (1, 5, 'Greeting', NULL, 700000000, 10, 0, NULL, NULL);
            INSERT INTO ZICCLOUDSYNCINGOBJECT \(cols) VALUES (2, 5, 'No date', NULL, 0, 10, 0, NULL, NULL);
            INSERT INTO ZICCLOUDSYNCINGOBJECT \(cols) VALUES (4, 5, 'Blank', NULL, 650000000, 10, 0, NULL, NULL);
            INSERT INTO ZICCLOUDSYNCINGOBJECT \(cols) VALUES (5, 5, 'Binned', NULL, 690000000, 10, 1, NULL, NULL);
            INSERT INTO ZICCLOUDSYNCINGOBJECT \(cols) VALUES (6, 5, 'Trashed', NULL, 695000000, 11, 0, NULL, NULL);
            INSERT INTO ZICCLOUDSYNCINGOBJECT \(cols) VALUES (7, 5, 'In a deleted folder', NULL, 696000000, 12, 0, NULL, NULL);
            INSERT INTO ZICCLOUDSYNCINGOBJECT \(cols) VALUES (8, 5, 'Short', NULL, 680000000, 10, 0, NULL, NULL);
            INSERT INTO ZICCLOUDSYNCINGOBJECT \(cols) VALUES (9, 5, 'Week', NULL, 670000000, 10, 0, NULL, NULL);
            INSERT INTO ZICCLOUDSYNCINGOBJECT (Z_PK, Z_ENT, ZNOTE, ZTYPEUTI, ZMERGEABLEDATA1, ZMARKEDFORDELETION)
                VALUES (20, 7, 9, 'com.apple.notes.table', X'\(hex(table))', 0);
            INSERT INTO ZICNOTEDATA VALUES (1, X'\(hex(store(greeting)))');
            INSERT INTO ZICNOTEDATA VALUES (2, X'\(hex(store("No date\nStill kept")))');
            INSERT INTO ZICNOTEDATA VALUES (3, X'\(hex(store(plumber)))');
            INSERT INTO ZICNOTEDATA VALUES (4, X'');
            INSERT INTO ZICNOTEDATA VALUES (5, X'\(hex(store("This one is left out")))');
            INSERT INTO ZICNOTEDATA VALUES (6, X'\(hex(store("Trashed and left out")))');
            INSERT INTO ZICNOTEDATA VALUES (7, X'\(hex(store("Deleted with its folder")))');
            INSERT INTO ZICNOTEDATA VALUES (8, X'\(hex(store("Ok", noteExtras: style, docExtras: ld(1, uuid))))');
            INSERT INTO ZICNOTEDATA VALUES (9, X'\(hex(store("Week\n\u{FFFC}", noteExtras: attachment)))');
            """
        let rowsPath = root.appendingPathComponent("Rows.sqlite").path
        check(sqlite3_open(rowsPath, &db) == SQLITE_OK, "fixture open")
        check(sqlite3_exec(db, currentTables + currentRows, nil, nil, nil) == SQLITE_OK, "fixture")
        sqlite3_close(db)
        let rows = R(storePath: rowsPath)
        let expected = [
            "1678307200\u{01}Greeting\u{01}Home\u{01}\(greeting)\u{02}",
            "1658307200\u{01}Short\u{01}Home\u{01}Ok\u{02}",
            "1648307200\u{01}Week\u{01}Home\u{01}Week\nMonday\nSwim\nTuesday\nRun\u{02}",
            "1578307200\u{01}Plumber\u{01}\u{01}\(plumber)\u{02}",
            "0\u{01}No date\u{01}Home\u{01}No date\nStill kept\u{02}",
        ].joined()
        let got = rows.readNotes()
        check(got == expected, "newest first, trash and binned left out, undated kept: \(String(describing: got))")
        check(rows.lastFailure == nil, "a complete read has no failure")

        // An older layout without the folder type: the English name decides.
        let legacyRows = """
            INSERT INTO ZICCLOUDSYNCINGOBJECT VALUES (10, 3, NULL, 'Home', NULL, NULL, 0);
            INSERT INTO ZICCLOUDSYNCINGOBJECT VALUES (11, 3, NULL, 'Recently Deleted', NULL, NULL, 0);
            INSERT INTO ZICCLOUDSYNCINGOBJECT VALUES (1, 5, 'Kept', NULL, 700000000, 10, 0);
            INSERT INTO ZICCLOUDSYNCINGOBJECT VALUES (2, 5, 'Trashed', NULL, 710000000, 11, 0);
            INSERT INTO ZICNOTEDATA VALUES (1, X'\(hex(store("Kept")))');
            INSERT INTO ZICNOTEDATA VALUES (2, X'\(hex(store("Trashed")))');
            """
        let legacyPath = root.appendingPathComponent("Legacy.sqlite").path
        check(sqlite3_open(legacyPath, &db) == SQLITE_OK, "fixture open")
        check(sqlite3_exec(db, noteTables + legacyRows, nil, nil, nil) == SQLITE_OK, "fixture")
        sqlite3_close(db)
        check(R(storePath: legacyPath).readNotes() == "1678307200\u{01}Kept\u{01}Home\u{01}Kept\u{02}",
              "an older store still leaves Recently Deleted out")

        // The same store with every page after the first two blanked: it opens
        // and its tables are known, but the read stops before any row. That is
        // a failed read with the plain reason, never an empty or shorter Notes.
        let damagedURL = root.appendingPathComponent("Damaged.sqlite")
        check(sqlite3_open(damagedURL.path, &db) == SQLITE_OK, "fixture open")
        check(sqlite3_exec(db, currentTables + currentRows, nil, nil, nil) == SQLITE_OK, "fixture")
        sqlite3_close(db)
        var bytes = try Data(contentsOf: damagedURL)
        let pageSize = (Int(bytes[16]) << 8) | Int(bytes[17])
        check(pageSize >= 512 && bytes.count > 2 * pageSize, "the fixture has pages past its first two")
        bytes.resetBytes(in: (2 * pageSize)..<bytes.count)
        try bytes.write(to: damagedURL)
        let damaged = R(storePath: damagedURL.path)
        check(damaged.readNotes() == nil, "a read that stops early is not an empty Notes")
        check(damaged.lastFailure?.kind == "" && damaged.lastFailure?.reason.contains("just now") == true,
              "a read that stops early keeps the plain reason")
        print("synthetic Notes store checks passed")
    }
}
