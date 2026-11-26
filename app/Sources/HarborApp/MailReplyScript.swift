import Foundation

/// One native envelope contract used by both preparation and execution.
/// No source-message sender/subject is mislabeled as outgoing metadata.
enum MailReplyScript {
    static let fields = ["message_id", "account", "account_id", "from", "to", "cc", "bcc", "subject", "content"]

    // Compare character IDs, not AppleScript's linguistic string equality.
    // Recursion covers every element of To/CC/BCC as well as scalar fields.
    static let exactValueHandler = """
    on exactReplyValue(actualValue, approvedValue)
        if (class of actualValue) is not (class of approvedValue) then return false
        if (class of actualValue) is list then
            if (count of actualValue) is not (count of approvedValue) then return false
            repeat with i from 1 to count of actualValue
                if not my exactReplyValue(item i of actualValue, item i of approvedValue) then return false
            end repeat
            return true
        end if
        if (class of actualValue) is not text then return false
        return (id of actualValue) is equal to (id of approvedValue)
    end exactReplyValue
    """

    private static func quote(_ value: String) -> String {
        "\"" + value.replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "\"", with: "\\\"")
            .replacingOccurrences(of: "\r", with: "\\r")
            .replacingOccurrences(of: "\n", with: "\\n") + "\""
    }

    static func literal(_ value: Any) -> String? {
        if let s = value as? String { return quote(s) }
        if let a = value as? [String] { return "{" + a.map(quote).joined(separator: ", ") + "}" }
        return nil
    }

    static func build(messageID: String, account: String, body: String,
                      replyAll: Bool, expected: [String: Any]? = nil,
                      sourceAccountID: String = "") -> String? {
        guard !messageID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              !body.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
        let accountFilter: String
        if let id = expected?["account_id"] as? String, !id.isEmpty {
            accountFilter = "(every account whose id is \(quote(id)))"
        } else if !sourceAccountID.isEmpty {
            accountFilter = "(every account whose id is \(quote(sourceAccountID)))"
        } else if !account.isEmpty {
            accountFilter = "(every account whose name is \(quote(account)))"
        } else {
            accountFilter = "accounts"
        }
        var finish = "close theReply saving no"
        if let expected {
            let values = fields.compactMap { expected[$0].flatMap(literal) }
            guard values.count == fields.count else { return nil }
            // Compare inside the same script immediately before send, including
            // actual From, full recipient sets and the entire rendered content.
            finish = """
            if not my exactReplyValue(envelope, {\(values.joined(separator: ", "))}) then
                error "The reply changed after approval. Nothing sent; prepare it again."
            end if
            if (send theReply) is not true then error "Mail did not accept the reply for sending; check Mail before retrying."
            """
        }
        return """
        \(exactValueHandler)
        tell application "Mail"
            set theMsg to missing value
            set theAcct to ""
            set theAcctID to ""
            set sourceAddresses to {}
            set hitCount to 0
            set matchingAccounts to \(accountFilter)
            repeat with acct in matchingAccounts
                -- Use the same mailbox vocabulary as MailReader. A failure to
                -- read any searched account prevents an ungrounded unique hit.
                try
                    set mbx to mailbox "INBOX" of acct
                    set hits to (every message of mbx whose message id is \(quote(messageID)))
                on error
                    set mbx to mailbox "Inbox" of acct
                    set hits to (every message of mbx whose message id is \(quote(messageID)))
                end try
                set hitCount to hitCount + (count of hits)
                if (count of hits) > 0 then
                    set theMsg to item 1 of hits
                    set theAcct to name of acct
                    set theAcctID to id of acct
                    set sourceAddresses to email addresses of acct
                end if
            end repeat
            if hitCount is not 1 then error "Choose one current email in one account; it was moved, missing, or ambiguous."
            if not my exactReplyValue((message id of theMsg) as text, \(quote(messageID))) then error "The source Message-ID changed. Refresh Mail."
            if \(quote(account)) is not "" then
                if not my exactReplyValue(theAcct as text, \(quote(account))) then error "The source account changed. Refresh Mail."
            end if
            if \(quote(sourceAccountID)) is not "" then
                if not my exactReplyValue(theAcctID as text, \(quote(sourceAccountID))) then error "The source account identity changed. Refresh Mail."
            end if
            set theReply to reply theMsg opening window true reply to all \(replyAll ? "true" : "false")
            try
                -- Mail can return a reply backend whose content stays empty
                -- and silently ignores writes. Explicit visibility initializes
                -- it; the reply command's opening-window flag alone does not.
                set visible of theReply to true
                delay 1
                -- Do not read content before setting it. Mail's rich-text
                -- getter can retain that first (empty) value for this reply.
                -- The supplied body is complete; threading belongs to Mail's
                -- reply metadata, not to a manually copied quote.
                set desiredContent to \(quote(body))
                tell theReply
                    set content to desiredContent
                end tell
                set fromAddress to extract address from (sender of theReply)
                ignoring case
                    if fromAddress is not in sourceAddresses then error "Mail selected a sending identity outside the chosen account. Nothing sent."
                end ignoring
                set toAddresses to address of every to recipient of theReply
                set ccAddresses to address of every cc recipient of theReply
                set bccAddresses to address of every bcc recipient of theReply
                set replySubject to (subject of theReply) as text
                -- Read content last, after the other native property reads.
                set renderedContent to (content of theReply) as text
                if (length of renderedContent) < (length of desiredContent) then error "Mail did not retain the requested reply text. Nothing sent."
                if not my exactReplyValue(text 1 thru (length of desiredContent) of renderedContent, desiredContent) then error "Mail did not retain the requested reply text. Nothing sent."
                set envelope to {(message id of theMsg) as text, theAcct as text, theAcctID as text, fromAddress as text, toAddresses, ccAddresses, bccAddresses, replySubject, renderedContent}
                \(finish)
                return envelope
            on error problem number code
                try
                    close theReply saving no
                end try
                error problem number code
            end try
        end tell
        """
    }

    static func decode(_ result: NSAppleEventDescriptor) -> [String: Any]? {
        guard result.numberOfItems == fields.count else { return nil }
        var envelope: [String: Any] = [:]
        for (index, field) in fields.enumerated() {
            guard let item = result.atIndex(index + 1) else { return nil }
            if ["to", "cc", "bcc"].contains(field) {
                guard item.descriptorType == NSAppleEventDescriptor.list().descriptorType else { return nil }
                var addresses: [String] = []
                if item.numberOfItems > 0 {
                    for i in 1...item.numberOfItems {
                        guard let value = item.atIndex(i)?.stringValue else { return nil }
                        addresses.append(value)
                    }
                }
                envelope[field] = addresses
            } else {
                guard let value = item.stringValue else { return nil }
                envelope[field] = value
            }
        }
        return envelope
    }
}

/// Script text for the plain Mail actions OutboundSender runs. Kept here, free
/// of app types, so the native contract check builds and compiles the same
/// text the app executes.
enum MailActionScript {
    /// sandbox/outbound.py mirrors this text; change both together.
    static let messageNotFound = "couldn't find that message in the inbox — it may have been moved or is older than the synced window"

    /// Raised when Mail's `send` answers false. Mail may still hold the
    /// message, so the wording must not invite a blind second send.
    static let sendNotAccepted = "Mail did not accept the email for sending; check Mail before retrying."

    // AppleScript string literals: escape backslashes first, then quotes, or
    // the backslashes introduced by quote-escaping get escaped a second time.
    // Newlines become \n escapes so a multi-paragraph email body stays a single
    // valid literal instead of breaking the script into unparseable lines.
    static func escape(_ s: String) -> String {
        s.replacingOccurrences(of: "\\", with: "\\\\")
         .replacingOccurrences(of: "\"", with: "\\\"")
         .replacingOccurrences(of: "\r\n", with: "\\n")
         .replacingOccurrences(of: "\r", with: "\\n")
         .replacingOccurrences(of: "\n", with: "\\n")
    }

    /// Finds one inbox message by its RFC Message-ID and leaves it in `theMsg`
    /// (and the account's name in `theAcct`), or raises `messageNotFound`.
    /// Goes inside a `tell application "Mail"` block.
    ///
    /// An account has no `inbox` property in Mail's dictionary (only the
    /// application and a message viewer do), so this names the mailbox the way
    /// MailReader and the reply script do: "INBOX", then "Inbox". The outer
    /// `try` skips an account that has neither instead of ending the search.
    /// `account` pins the search to one account by name; without it the first
    /// account holding the Message-ID wins.
    static func findInboxMessage(messageID: String, account: String = "") -> String {
        let accountList = account.isEmpty
            ? "accounts"
            : "(every account whose name is \"\(escape(account))\")"
        let wanted = "\"\(escape(messageID))\""
        return """
            set theMsg to missing value
            set theAcct to ""
            repeat with acct in \(accountList)
                if theMsg is missing value then
                    try
                        try
                            set mbx to mailbox "INBOX" of acct
                            set hits to (every message of mbx whose message id is \(wanted))
                        on error
                            set mbx to mailbox "Inbox" of acct
                            set hits to (every message of mbx whose message id is \(wanted))
                        end try
                        if (count of hits) > 0 then
                            set theMsg to item 1 of hits
                            set theAcct to (name of acct)
                        end if
                    end try
                end if
            end repeat
            if theMsg is missing value then error "\(messageNotFound)"
        """
    }

    private static func recipientLines(_ kind: String, _ addresses: [String]) -> [String] {
        addresses.map {
            "make new \(kind) recipient at end of \(kind) recipients with properties {address:\"\(escape($0))\"}"
        }
    }

    /// A new message, sent without a window. Mail's `send` answers a boolean
    /// and raises nothing when it refuses, so a false answer is made an error
    /// here; otherwise the caller would report an email that never left.
    static func sendEmail(to: [String], cc: [String], subject: String, body: String) -> String {
        let recipients = (recipientLines("to", to) + recipientLines("cc", cc)).joined(separator: "\n")
        return """
        tell application "Mail"
            set newMsg to make new outgoing message with properties {subject:"\(escape(subject))", content:"\(escape(body))", visible:false}
            tell newMsg
        \(recipients)
                if (send) is not true then error "\(sendNotAccepted)"
            end tell
        end tell
        """
    }

    /// Raised before `send` when Mail's forward lost or never loaded the
    /// original email. Nothing is sent; the window is closed unsaved.
    static let forwardLostOriginal = "Mail did not keep the original email in the forward. Nothing sent."
    /// Raised before `send` when the forward's recipients are not exactly the approved ones.
    static let forwardRecipientsChanged = "Mail changed the forward's recipients. Nothing sent."
    /// Raised before `send` when Mail picked a sending address outside the account that holds the email.
    static let forwardIdentityChanged = "Mail selected a sending identity outside the email's account. Nothing sent."

    /// Raised before the forward window opens when a note would replace an email
    /// that has attachments: setting a forward's content drops them.
    static let forwardNoteDropsAttachments = "Mail would drop this email's attachments if a note is added. Nothing sent."
    /// Raised after Mail answered false to a forward's `send`; the unsent window is closed first.
    static let forwardSendNotAccepted = "Mail did not accept the forward for sending and its window was closed; check Mail's Outbox before retrying."

    /// The error number every check the forward script makes before `send`
    /// raises (and nothing else in it), so the app can tell the backend that
    /// nothing was sent: OutboundSender passes `preSendCheck` on.
    static let checkedBeforeSendNumber = 9301
    private static let preSendChecks: [(String, String)] = [
        (messageNotFound, "not_found"), (forwardLostOriginal, "lost_original"),
        (forwardRecipientsChanged, "recipients_changed"), (forwardIdentityChanged, "identity_changed"),
        (forwardNoteDropsAttachments, "note_drops_attachments"),
    ]

    /// What the backend is told about a forward that stopped at one of its own
    /// checks before `send`: which check (never Mail's text). Empty for any
    /// other error, which stays an unknown outcome.
    static func preSendCheck(code: Int, message: String) -> [String: Any] {
        guard code == checkedBeforeSendNumber,
              let check = preSendChecks.first(where: { $0.0 == message })?.1 else { return [:] }
        return ["refusal": "checked_before_send", "check": check]
    }

    /// The fields of the receipt a forward script returns after Mail accepted
    /// the send, in order. service/tools/action_tools.py reads it.
    static let forwardFields = ["message_id", "account", "from", "to", "subject"]

    /// Forwards one inbox message, found by its RFC Message-ID, to `to`, with
    /// an optional note above the original.
    ///
    /// The lessons of the reply script apply: the compose window is made
    /// visible and given a moment before its content is read, because Mail can
    /// hand back an empty body otherwise. A forward whose original is empty, or
    /// whose note replaced the original instead of sitting above it, is closed
    /// unsaved before `send`, so a note can never go out alone. Without a note
    /// the content is never touched, so the original's formatting and
    /// attachments go as Mail forwards them; a note replaces the content with
    /// text (the formatting is lost), so a note on an email with attachments
    /// is refused before the window opens. Every check before `send` raises
    /// `checkedBeforeSendNumber`; a false answer to `send` closes the unsent
    /// window. The recipients
    /// and the sending address are read back and checked before `send`. Only
    /// after Mail answers true to `send` does the script return its receipt
    /// (`forwardFields`); anything else is an error.
    static func forwardEmail(messageID: String, account: String = "", to: [String], note: String) -> String {
        let accountList = account.isEmpty
            ? "accounts"
            : "(every account whose name is \"\(escape(account))\")"
        let wanted = "\"\(escape(messageID))\""
        let recipients = recipientLines("to", to).map { "            " + $0 }.joined(separator: "\n")
        let approved = "{" + to.map { "\"\(escape($0))\"" }.joined(separator: ", ") + "}"
        // Setting a forward's content keeps only its text: with a note, an email
        // with attachments is refused before the window opens, not sent without them.
        let attachmentCheck = note.isEmpty ? "" :
            "if (count of mail attachments of theMsg) > 0 then error \"\(forwardNoteDropsAttachments)\" number \(checkedBeforeSendNumber)"
        let content = note.isEmpty ? "" : """
                set desiredNote to "\(escape(note))"
                set content of fwd to desiredNote & return & return & originalContent
                set renderedContent to (content of fwd) as text
                if (length of renderedContent) < ((length of desiredNote) + (length of originalContent)) then error "\(forwardLostOriginal)" number \(checkedBeforeSendNumber)
                if not my exactReplyValue(text 1 thru (length of desiredNote) of renderedContent, desiredNote) then error "\(forwardLostOriginal)" number \(checkedBeforeSendNumber)
        """
        return """
        \(MailReplyScript.exactValueHandler)
        tell application "Mail"
            set theMsg to missing value
            set theAcct to ""
            set sourceAddresses to {}
            repeat with acct in \(accountList)
                if theMsg is missing value then
                    try
                        try
                            set mbx to mailbox "INBOX" of acct
                            set hits to (every message of mbx whose message id is \(wanted))
                        on error
                            set mbx to mailbox "Inbox" of acct
                            set hits to (every message of mbx whose message id is \(wanted))
                        end try
                        if (count of hits) > 0 then
                            set theMsg to item 1 of hits
                            set theAcct to (name of acct)
                            set sourceAddresses to email addresses of acct
                        end if
                    end try
                end if
            end repeat
            if theMsg is missing value then error "\(messageNotFound)" number \(checkedBeforeSendNumber)
            if not my exactReplyValue((message id of theMsg) as text, \(wanted)) then error "\(messageNotFound)" number \(checkedBeforeSendNumber)
            \(attachmentCheck)
            set fwd to forward theMsg with opening window
            try
                -- As with a reply: a visible window, then a moment, before
                -- Mail's content getter returns the forwarded original.
                set visible of fwd to true
                delay 1
                set originalContent to (content of fwd) as text
                if (length of originalContent) is 0 then error "\(forwardLostOriginal)" number \(checkedBeforeSendNumber)
                tell fwd
        \(recipients)
                end tell
        \(content)
                set fromAddress to extract address from (sender of fwd)
                ignoring case
                    if fromAddress is not in sourceAddresses then error "\(forwardIdentityChanged)" number \(checkedBeforeSendNumber)
                end ignoring
                set toAddresses to address of every to recipient of fwd
                set approvedAddresses to \(approved)
                if (count of toAddresses) is not (count of approvedAddresses) then error "\(forwardRecipientsChanged)" number \(checkedBeforeSendNumber)
                ignoring case
                    repeat with wantedAddress in approvedAddresses
                        if (contents of wantedAddress) is not in toAddresses then error "\(forwardRecipientsChanged)" number \(checkedBeforeSendNumber)
                    end repeat
                end ignoring
                set receipt to {(message id of theMsg) as text, theAcct as text, fromAddress as text, toAddresses, (subject of fwd) as text}
            on error problem number code
                try
                    close fwd saving no
                end try
                error problem number code
            end try
            tell fwd
                set sendAnswer to (send)
            end tell
            -- Mail answers false without raising when it refuses: the forward
            -- is still open and unsent, so close it rather than leave it.
            if sendAnswer is not true then
                try
                    close fwd saving no
                end try
                error "\(forwardSendNotAccepted)"
            end if
            return receipt
        end tell
        """
    }

    /// The receipt a forward script returned, or nil when it is not exactly
    /// `forwardFields` (the backend then reports the outcome as unknown).
    static func decodeForward(_ result: NSAppleEventDescriptor) -> [String: Any]? {
        guard result.descriptorType == NSAppleEventDescriptor.list().descriptorType,
              result.numberOfItems == forwardFields.count else { return nil }
        var receipt: [String: Any] = [:]
        for (index, field) in forwardFields.enumerated() {
            guard let item = result.atIndex(index + 1) else { return nil }
            if field == "to" {
                guard item.descriptorType == NSAppleEventDescriptor.list().descriptorType,
                      item.numberOfItems > 0 else { return nil }
                var addresses: [String] = []
                for i in 1...item.numberOfItems {
                    guard let value = item.atIndex(i)?.stringValue else { return nil }
                    addresses.append(value)
                }
                receipt[field] = addresses
            } else {
                guard let value = item.stringValue else { return nil }
                receipt[field] = value
            }
        }
        return receipt
    }
}
