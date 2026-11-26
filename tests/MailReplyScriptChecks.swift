import Foundation

// Standalone so the native contract checks also run with Command Line Tools
// installations that do not include XCTest. Mail scripts are compiled only.
// Only the pure literal-value comparator is executed; it cannot contact Mail.
@main
enum MailReplyScriptChecks {
    static func require(_ value: @autoclosure () -> Bool, _ message: String) {
        guard value() else { fatalError(message) }
    }

    static func main() {
        let envelope: [String: Any] = [
            "message_id": "<fixture@example.com>", "account": "Work", "account_id": "account-1",
            "from": "me@example.com", "to": ["reply@example.com"], "cc": [], "bcc": [],
            "subject": "Re: A \"quoted\" subject", "content": "Thanks\rOriginal\nline \\ path",
        ]
        let prepare = MailReplyScript.build(messageID: "<fixture@example.com>", account: "Work",
                                            body: "Thanks", replyAll: false)!
        require(!prepare.contains("send theReply"), "Preparation can send")
        for required in ["close theReply saving no", "sender of theReply", "subject of theReply",
                         "bcc recipient of theReply", "hitCount is not 1", "set visible of theReply to true",
                         "exactReplyValue(text 1 thru"] {
            require(prepare.contains(required), "Missing contract: \(required)")
        }
        let send = MailReplyScript.build(messageID: "<fixture@example.com>", account: "Work",
                                         body: "Thanks", replyAll: true, expected: envelope)!
        require(prepare.range(of: "set content to desiredContent")!.lowerBound <
                prepare.range(of: "(content of theReply)")!.lowerBound,
                "Reading reply content before assigning it can cache an empty native body")
        require(send.range(of: "exactReplyValue(envelope,")!.lowerBound < send.range(of: "send theReply")!.lowerBound,
                "Send happens before validation")
        require(send.contains("every account whose id is"), "Account is not pinned by ID")
        require(send.contains("if (send theReply) is not true"), "Mail's false result is ignored")
        require(send.contains("id of actualValue"), "Exact character comparison required")
        let pinned = MailReplyScript.build(messageID: "<fixture@example.com>", account: "Work",
            body: "Thanks", replyAll: false, sourceAccountID: "native-source-id")!
        require(pinned.contains("every account whose id is \"native-source-id\""),
                "Preparation must use the source snapshot's account ID")
        require(!pinned.contains("send theReply"), "Pinned preparation can send")
        require(MailReplyScript.build(messageID: "id", account: "Work", body: "Thanks", replyAll: false,
                                      expected: ["message_id": "id"]) == nil, "Incomplete envelope accepted")
        let result = NSAppleEventDescriptor.list()
        for (index, field) in MailReplyScript.fields.enumerated() {
            let value: NSAppleEventDescriptor
            if let addresses = envelope[field] as? [String] {
                value = .list()
                for (i, address) in addresses.enumerated() { value.insert(.init(string: address), at: i + 1) }
            } else { value = .init(string: envelope[field] as! String) }
            result.insert(value, at: index + 1)
        }
        let decoded = MailReplyScript.decode(result)!
        require(decoded["to"] as? [String] == ["reply@example.com"], "Recipient list decode failed")
        require(decoded["content"] as? String == envelope["content"] as? String, "Content decode changed text")
        for expected in [nil, envelope] as [[String: Any]?] {
            let source = MailReplyScript.build(messageID: "<fixture@example.com>", account: "Work",
                body: "A \"quote\"\nBackslash \\ and tab\t.", replyAll: true, expected: expected)!
            let script = NSAppleScript(source: source)!
            var error: NSDictionary?
            require(script.compileAndReturnError(&error), "Mail script failed to compile: \(error ?? [:])")
        }

        // Mark read, flag and archive find their message with this block. An
        // account has no `inbox` property in Mail's dictionary, and a compile
        // check passes either spelling, so the text itself is asserted.
        let lookup = MailActionScript.findInboxMessage(messageID: "<fixture@example.com>")
        require(lookup.contains("repeat with acct in accounts"), "Unpinned lookup must walk every account")
        require(lookup.contains("set mbx to mailbox \"INBOX\" of acct"), "Lookup must name the INBOX mailbox")
        require(lookup.contains("set mbx to mailbox \"Inbox\" of acct"), "Lookup must retry the Inbox spelling")
        require(lookup.range(of: "mailbox \"INBOX\" of acct")!.lowerBound <
                lookup.range(of: "mailbox \"Inbox\" of acct")!.lowerBound, "Inbox is the retry, not the first try")
        require(!lookup.contains("inbox of acct"), "An account has no inbox property; the lookup would find nothing")
        require(lookup.contains("message id is \"<fixture@example.com>\""), "Lookup must match the Message-ID")
        require(lookup.contains("if theMsg is missing value then error \"\(MailActionScript.messageNotFound)\""),
                "A missing message must stop the script before any change")
        require(MailActionScript.messageNotFound ==
                "couldn't find that message in the inbox — it may have been moved or is older than the synced window",
                "sandbox/outbound.py mirrors the not-found text")
        let pinnedLookup = MailActionScript.findInboxMessage(messageID: "a\"b\\c", account: "Work \"two\"")
        require(pinnedLookup.contains("repeat with acct in (every account whose name is \"Work \\\"two\\\"\")"),
                "A pinned lookup must search only the named account, with the name escaped")
        require(pinnedLookup.contains("message id is \"a\\\"b\\\\c\""), "Message-ID must be escaped")
        for block in [lookup, pinnedLookup] {
            let source = "tell application \"Mail\"\n" + block
                + "\nset read status of theMsg to true\nreturn theAcct\nend tell"
            var error: NSDictionary?
            require(NSAppleScript(source: source)!.compileAndReturnError(&error),
                    "Mail lookup script failed to compile: \(error ?? [:])")
        }

        // Mail's `send` answers false without raising when it refuses. A
        // plain send and a forward must turn that answer into an error, or
        // the app reports an email that never left.
        let sendGuard = "if (send) is not true then error \"\(MailActionScript.sendNotAccepted)\""
        func hasBareSend(_ script: String) -> Bool {
            script.components(separatedBy: "\n").contains { $0.trimmingCharacters(in: .whitespaces) == "send" }
        }
        let plainSend = MailActionScript.sendEmail(to: ["first@example.com", "second@example.com"],
            cc: ["third@example.com"], subject: "A \"quoted\" subject", body: "Line one\nLine two \\ end")
        require(plainSend.contains(sendGuard) && !hasBareSend(plainSend), "A plain send ignores Mail's false result")
        require(plainSend.range(of: "tell newMsg")!.lowerBound < plainSend.range(of: sendGuard)!.lowerBound,
                "Send must resolve against the new outgoing message")
        require(plainSend.contains("visible:false"), "A plain send must not open a window")
        for address in ["first@example.com", "second@example.com"] {
            require(plainSend.contains("make new to recipient at end of to recipients with properties {address:\"\(address)\"}"),
                    "Missing To recipient")
        }
        require(plainSend.contains("make new cc recipient at end of cc recipients with properties {address:\"third@example.com\"}"),
                "Missing CC recipient")
        require(plainSend.contains("subject:\"A \\\"quoted\\\" subject\", content:\"Line one\\nLine two \\\\ end\""),
                "Subject and body must be escaped into single literals")
        let forwardWithNote = MailActionScript.forwardEmail(messageID: "<fixture@example.com>",
            to: ["first@example.com"], note: "For you")
        let forwardBare = MailActionScript.forwardEmail(messageID: "<fixture@example.com>",
            to: ["first@example.com", "second@example.com"], note: "")
        func at(_ script: String, _ text: String) -> String.Index {
            guard let range = script.range(of: text) else { fatalError("Missing forward contract: \(text)") }
            return range.lowerBound
        }
        // A forward that Mail refuses to send is closed, not left open on screen.
        let forwardSend = "set sendAnswer to (send)"
        let forwardGuard = "if sendAnswer is not true then"
        for forward in [forwardWithNote, forwardBare] {
            require(forward.contains(forwardSend) && !hasBareSend(forward), "A forward ignores Mail's false result")
            require(forward.range(of: "tell fwd", options: .backwards)!.lowerBound < at(forward, forwardSend)
                    && at(forward, forwardSend) < at(forward, forwardGuard),
                    "Send must resolve against the forwarded message, and its answer be checked")
            require(at(forward, forwardGuard) < forward.range(of: "close fwd saving no", options: .backwards)!.lowerBound
                    && forward.range(of: "close fwd saving no", options: .backwards)!.lowerBound
                        < at(forward, "error \"\(MailActionScript.forwardSendNotAccepted)\""),
                    "A forward Mail refused to send is left open")
            // Every check before send says so by its number, and only those do.
            let numbered = forward.components(separatedBy: " number \(MailActionScript.checkedBeforeSendNumber)").count - 1
            require(numbered == (forward == forwardWithNote ? 9 : 6), "A check before send is not marked")
            require(!forward.contains("\(MailActionScript.forwardSendNotAccepted)\" number"),
                    "A refused send must not claim it was stopped before send")
            require(at(forward, MailActionScript.messageNotFound) < at(forward, "forward theMsg"),
                    "A forward must find its message first")
            require(!forward.contains("inbox of acct"), "An account has no inbox property; the lookup would find nothing")
            require(at(forward, "mailbox \"INBOX\" of acct") < at(forward, "mailbox \"Inbox\" of acct"),
                    "The forward lookup names the inbox the way the reader does")
            // Visible and given a moment before the original is read.
            require(at(forward, "set visible of fwd to true") < at(forward, "delay 1"), "Forward window is not made visible first")
            require(at(forward, "delay 1") < at(forward, "set originalContent to (content of fwd)"),
                    "The original is read before Mail loaded it")
            require(forward.contains("if (length of originalContent) is 0 then error \"\(MailActionScript.forwardLostOriginal)\""),
                    "An empty forward must stop before send")
            // Every check comes before send, and a failure closes the window unsaved.
            for check in ["sender of fwd", "address of every to recipient of fwd",
                          MailActionScript.forwardRecipientsChanged, MailActionScript.forwardIdentityChanged,
                          "set receipt to {"] {
                require(at(forward, check) < at(forward, forwardGuard), "Checked after send: \(check)")
            }
            require(at(forward, "close fwd saving no") < at(forward, forwardGuard), "A failed check leaves the forward open")
            require(at(forward, forwardGuard) < at(forward, "return receipt"), "A receipt is returned before Mail accepted the send")
        }
        require(forwardWithNote.contains("set desiredNote to \"For you\""), "Missing forward note")
        require(at(forwardWithNote, "set content of fwd to desiredNote & return & return & originalContent") <
                at(forwardWithNote, "exactReplyValue(text 1 thru (length of desiredNote) of renderedContent, desiredNote)"),
                "The note is not checked above the original")
        require(forwardWithNote.contains("((length of desiredNote) + (length of originalContent))"),
                "A note that replaced the original would be sent alone")
        require(!forwardBare.contains("set content of fwd"), "A forward without a note must leave the content alone")
        require(at(forwardWithNote, "count of mail attachments of theMsg") < at(forwardWithNote, "forward theMsg"),
                "A note on an email with attachments must stop before the forward opens")
        require(!forwardBare.contains("mail attachments"), "A forward without a note keeps its attachments")
        for (message, check) in [(MailActionScript.messageNotFound, "not_found"),
                                 (MailActionScript.forwardLostOriginal, "lost_original"),
                                 (MailActionScript.forwardRecipientsChanged, "recipients_changed"),
                                 (MailActionScript.forwardIdentityChanged, "identity_changed"),
                                 (MailActionScript.forwardNoteDropsAttachments, "note_drops_attachments")] {
            let told = MailActionScript.preSendCheck(code: MailActionScript.checkedBeforeSendNumber, message: message)
            require(told["refusal"] as? String == "checked_before_send" && told["check"] as? String == check,
                    "A check before send is not passed on: \(check)")
            require(MailActionScript.preSendCheck(code: -1712, message: message).isEmpty,
                    "Only the checks' own number counts")
        }
        require(MailActionScript.preSendCheck(code: MailActionScript.checkedBeforeSendNumber,
                                              message: MailActionScript.forwardSendNotAccepted).isEmpty,
                "A refused send is never proof nothing was sent")
        require(forwardBare.contains("set approvedAddresses to {\"first@example.com\", \"second@example.com\"}"),
                "The approved recipients are not checked")
        let escapedForward = MailActionScript.forwardEmail(messageID: "a\"b\\c", account: "Work \"two\"",
            to: ["x\"y@example.com"], note: "Line one\nLine \"two\"")
        require(escapedForward.contains("message id is \"a\\\"b\\\\c\""), "Forward Message-ID must be escaped")
        require(escapedForward.contains("repeat with acct in (every account whose name is \"Work \\\"two\\\"\")"),
                "A pinned forward must search only the named account")
        require(escapedForward.contains("set desiredNote to \"Line one\\nLine \\\"two\\\"\""), "Forward note must be escaped")
        // Receipt decode: exactly the five fields, with the recipients as a list.
        let receipt = NSAppleEventDescriptor.list()
        let receiptValues: [Any] = ["<fixture@example.com>", "Work", "me@example.com",
                                    ["first@example.com"], "Fwd: A subject"]
        for (index, value) in receiptValues.enumerated() {
            if let list = value as? [String] {
                let item = NSAppleEventDescriptor.list()
                for (i, address) in list.enumerated() { item.insert(.init(string: address), at: i + 1) }
                receipt.insert(item, at: index + 1)
            } else {
                receipt.insert(.init(string: value as! String), at: index + 1)
            }
        }
        let decodedForward = MailActionScript.decodeForward(receipt)!
        require(decodedForward["to"] as? [String] == ["first@example.com"], "Forward recipients decode failed")
        require(decodedForward["message_id"] as? String == "<fixture@example.com>", "Forward Message-ID decode failed")
        let short = NSAppleEventDescriptor.list()
        short.insert(.init(string: "<fixture@example.com>"), at: 1)
        require(MailActionScript.decodeForward(short) == nil, "A partial receipt must not decode")
        require(MailActionScript.decodeForward(.init(string: "ok")) == nil, "A bare answer must not decode")
        var compileError: NSDictionary?
        require(NSAppleScript(source: escapedForward)!.compileAndReturnError(&compileError),
                "Escaped forward script failed to compile: \(compileError ?? [:])")
        for source in [plainSend, forwardWithNote, forwardBare] {
            var error: NSDictionary?
            require(NSAppleScript(source: source)!.compileAndReturnError(&error),
                    "Mail send script failed to compile: \(error ?? [:])")
        }

        func vector(_ value: [String: Any]) -> String {
            "{" + MailReplyScript.fields.map { MailReplyScript.literal(value[$0]!)! }.joined(separator: ", ") + "}"
        }
        func compare(_ actual: String, _ approved: String, matches: Bool) {
            if CommandLine.arguments.contains("--compile-only") { return }
            let source = MailReplyScript.exactValueHandler
                + "\nreturn my exactReplyValue(\(actual), \(approved))"
            require(!source.contains("tell application"), "Comparator probe can contact an application")
            var error: NSDictionary?
            let result = NSAppleScript(source: source)!.executeAndReturnError(&error)
            require(error == nil, "Pure comparator failed: \(error ?? [:])")
            require(result.booleanValue == matches, "Exact comparison returned an incorrect result")
        }
        var populated = envelope
        populated["cc"] = ["first@example.com", "second@example.com"]
        populated["bcc"] = ["private@example.com"]
        compare(vector(populated), vector(populated), matches: true)
        for field in MailReplyScript.fields {
            var changed = populated
            if let values = changed[field] as? [String] {
                changed[field] = values.map { $0.uppercased() }
            } else {
                changed[field] = (changed[field] as! String).uppercased()
            }
            compare(vector(changed), vector(populated), matches: false)
        }
        for cc in [["first@example.com"], ["second@example.com", "first@example.com"]] {
            var changed = populated
            changed["cc"] = cc
            compare(vector(changed), vector(populated), matches: false)
        }
        for (a, b) in [("x\ry", "x\ny"), ("x\\ny", "x\ny"), ("é", "e\u{301}"), ("A", "a")] {
            compare(MailReplyScript.literal([a])!, MailReplyScript.literal([b])!, matches: false)
        }
        compare("{}", "{}", matches: true)
        compare("\"\"", "\"\"", matches: true)
        compare("{}", "\"\"", matches: false)

        // Compile the actual raw-reader templates with their Swift-only
        // substitutions supplied. No reader instance or Mail action runs.
        let readerURL = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
            .deletingLastPathComponent().appendingPathComponent("app/Sources/HarborApp/MailReader.swift")
        let reader = try! String(contentsOf: readerURL, encoding: .utf8)
        func template(after marker: String) -> String {
            let markerEnd = reader.range(of: marker)!.upperBound
            let start = reader[markerEnd...].range(of: "\"\"\"")!.upperBound
            let end = reader[start...].range(of: "\"\"\"")!.lowerBound
            return String(reader[start..<end])
        }
        let inbox = template(after: "let capturedLabel = esc(account.label)")
            .replacingOccurrences(of: "\\(nativeID)", with: "native-source-id")
            .replacingOccurrences(of: "\\(capturedLabel)", with: "Work")
        let accounts = template(after: "private let accountsScript")
        func scanTemplate(_ marker: String) -> String {
            template(after: marker)
                .replacingOccurrences(of: "\\(refDateSetup)", with: template(after: "private let refDateSetup"))
                .replacingOccurrences(of: "\\(inboxSource(account))", with: inbox)
                .replacingOccurrences(of: "\\(acctNameFragment(account))", with: "set acctName to \"Work\"")
                .replacingOccurrences(of: "\\(esc(account?.id ?? \"\"))", with: "native-source-id")
                .replacingOccurrences(of: "\\(esc(account?.id ?? \"*\"))", with: "native-source-id")
                .replacingOccurrences(of: "\\(esc(account?.label ?? \"Mail\"))", with: "Work")
                .replacingOccurrences(of: "\\(capName)", with: "Work")
                .replacingOccurrences(of: "\\(HistoryMerge.appleScriptCutoff(cutoff))", with: "1")
                .replacingOccurrences(of: "\\(start)", with: "1")
                .replacingOccurrences(of: "\\(count)", with: "2")
                .replacingOccurrences(of: "\\(limit)", with: "2")
        }
        let raw = scanTemplate("private func rawScript")
        let header = scanTemplate("private func headerScript")
        let history = scanTemplate("private func historyBatchScript")
        require(inbox.contains("every account whose id is") && inbox.contains("count of matches"),
                "Native scans must select exactly one stable account ID")
        require(inbox.contains("enabled of scanAccount") && inbox.contains("name of scanAccount"),
                "Native scans must reject changed or disabled inventory targets")
        require(!inbox.contains("of account \"Work\"") && !reader.contains("accounts.first { $0.value == label }"),
                "Native scan identity must not be inferred from a display label")
        require(raw.contains("fieldText contains FS") && raw.contains("fieldText contains RS"),
                "Raw fields may corrupt record boundaries")
        require(raw.contains("acctID") && raw.contains("item 9 of rowFields"), "Raw account identity missing")
        require(!accounts.contains("try"), "Account enumeration can hide partial failures")
        require(reader.contains("duplicate_account_labels"),
                "Duplicate account labels need a distinct raw-coverage failure code")
        require(reader.contains("rename one of the duplicate account labels"),
                "Duplicate account labels need an actionable diagnostic")
        for source in [accounts, raw, header, history, "tell application \"Mail\"\n" + inbox + "\nend tell"] {
            var error: NSDictionary?
            require(NSAppleScript(source: source)!.compileAndReturnError(&error),
                    "Mail reader script failed to compile: \(error ?? [:])")
        }
        print("Mail reply native contract checks passed (scripts compiled, no Mail actions executed).")
    }
}
