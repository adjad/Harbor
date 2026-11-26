import Foundation

// Small pure rules behind a handful of app fixes, kept free of views, the
// network and the Keychain so tests/AppBugChecks.swift can compile them alone.

/// Study's text caps, counted as the service counts them: code points, not
/// Swift characters (one emoji or accented letter can be several code points).
enum ScalarCap {
    static func exceeds(_ text: String, _ limit: Int) -> Bool {
        text.unicodeScalars.count > limit
    }

    /// The first `limit` code points of `text` (all of it when it already fits).
    static func prefix(_ text: String, _ limit: Int) -> String {
        guard exceeds(text, limit) else { return text }
        return String(String.UnicodeScalarView(text.unicodeScalars.prefix(max(0, limit))))
    }
}

/// An Ideate reply belongs to the project that asked for it, not to whichever
/// project is open when it returns.
enum IdeateReply {
    static func applies(sentFor: String, open: String?) -> Bool { sentFor == open }
}

/// Replacing a saved secret without a moment where neither the old nor the new
/// one is saved. The new one goes in under a spare account first; only then is
/// the old item removed and the spare renamed into its place. Each step is the
/// caller's own Keychain call, so checks run it on a fake.
struct SecretReplacement {
    /// Saves the new secret under `account`; false when it couldn't.
    var add: (_ account: String) -> Bool
    /// Removes the item under `account`; true when none is left (or none was there).
    var remove: (_ account: String) -> Bool
    /// Moves the item from one account to another; false when it couldn't.
    var rename: (_ from: String, _ to: String) -> Bool

    static func spare(for account: String) -> String { account + ".pending" }

    /// True when the new secret is saved under `account`. On false the old one is
    /// still there, except when the Keychain refused both ways of putting the new
    /// one in place after the old was removed; the new one then stays under the spare.
    func replace(account: String) -> Bool {
        let spare = Self.spare(for: account)
        _ = remove(spare)                       // left over from an interrupted save
        guard add(spare) else { return false }  // the old item is untouched
        guard remove(account) else {
            _ = remove(spare)
            return false
        }
        if rename(spare, account) { return true }
        guard add(account) else { return false }
        _ = remove(spare)
        return true
    }
}

/// What an empty Memory list says. It depends on the filter, because an empty
/// "Corrections" or "Later" list says nothing about whether anything is saved.
enum MemoryEmptyText {
    /// `status` is the list's filter: active, superseded, proposed or deferred.
    static func line(status: String, query: String) -> String {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmed.isEmpty { return "No memories match “\(trimmed)”." }
        switch status {
        case "superseded": return "No corrections yet. When a memory replaces an older one, the older version shows here."
        case "proposed": return "Nothing to review. New suggestions appear here."
        case "deferred": return "Nothing set aside. Suggestions you choose Later on wait here."
        default: return "Nothing saved yet. Tell Harbor something to remember in Ask."
        }
    }
}

/// When a research view stops reconnecting.
enum ResearchReconnect {
    /// Failed reconnects in a row before giving up (about four minutes at the 8 s ceiling).
    static let maxFailures = 30
    static let goneLine = "This research isn't on this Mac any more."
    static let lostLine = "Harbor lost touch with this research. Your saved work is kept; open it again in a moment."

    /// Whether the event stream said the job itself is gone (404 or 410), which no retry will change.
    static func isGone(streamMessage: String) -> Bool {
        streamMessage == "Research returned 404." || streamMessage == "Research returned 410."
    }

    /// The count of failed reconnects after one more round: a round that read the
    /// job or received a new event starts it again.
    static func failures(after failures: Int, readSnapshot: Bool, newEvents: Bool) -> Int {
        readSnapshot || newEvents ? 0 : failures + 1
    }
}
