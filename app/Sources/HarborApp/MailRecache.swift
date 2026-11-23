import Foundation

/// When Google isn't covering mail (no Gmail account connected, or its sync is failing because the
/// Mac is offline or the sign-in expired), Harbor makes sure Mail.app's mail is read:
///
///   (a) Mail is open and Automation allowed → read it through AppleScript, as always;
///   (b) otherwise, with Full Disk Access → read the Envelope Index (a live read while Mail runs,
///       since it is fetching into it: "mail_fetch");
///   (c) no live read is fresh → open Mail hidden (no activation, not in recent items; needs no
///       permission). With Automation: wait until it answers, check for new mail, then run the
///       header, raw and history reads. Without it but with the index: wait until Mail stops writing
///       its index, then read the index. This runs after a good index read too.
///
/// The index only moves while Mail runs and fetches: closed, Mail downloads nothing, so the index
/// holds the mail as of Mail's last run. Unless Google covers mail, a read of it is never fresh on
/// its own: only a live read through Mail.app is (and only that clears "Couldn't check <account>"
/// in the service, service/proactive/cycle.py). So while any listed Google account is unchecked,
/// one healthy account doesn't count as covering mail either.
///
/// Never asks for a permission. Never opens Mail while the Mac is offline (it couldn't fetch); the
/// network coming back, or the Mac waking, checks again. Once Mail answers, Harbor tells it to check
/// for new mail and waits until it has finished fetching (or fetchTimeout) before reading.
/// Polite: opens Mail at most once every 30 minutes (once an hour outside the person's working
/// hours, weekends included), only when the mail cache is stale, never within 15 minutes of the person quitting Mail themselves, and afterwards
/// quits only a Mail that Harbor opened, with `terminate` (never forced), and not if the person has
/// since brought it forward or opened a window. Pure: MailReader supplies the facts.
enum MailRecache {
    /// Harbor opens Mail at most this often.
    static let openEvery: TimeInterval = 30 * 60
    /// Outside working hours (weekends included) Harbor opens Mail at most once an hour.
    static let offHoursOpenEvery: TimeInterval = 60 * 60
    /// The longest Harbor waits for a Mail it opened to finish fetching before reading anyway.
    static let fetchTimeout: TimeInterval = 5 * 60
    /// Mail must report no background activity this many polls in a row to count as done fetching
    /// (it can read 0 for a moment before a fetch starts).
    static let fetchIdlePolls = 2
    /// The person quit Mail within this long: leave it closed.
    static let userQuitGrace: TimeInterval = 15 * 60
    /// The mail cache counts as stale after this long without a successful read.
    static let staleAfter: TimeInterval = 30 * 60
    /// A Gmail account whose last header sync is older than this isn't covering mail (offline).
    static let gmailFreshFor: TimeInterval = 30 * 60
    /// How long Harbor waits for a Mail it opened to answer a script.
    static let answerTimeout: TimeInterval = 60
    /// The longest Harbor keeps a Mail it opened while the reads finish.
    static let recacheTimeout: TimeInterval = 15 * 60

    /// What Brief says while Harbor reads a Mail it opened.
    static let readingNote = "Reading Mail…"
    /// The reason_kind the service keys "Reading Mail…" on.
    static let readingKind = "mail_recaching"

    /// One Google account as the gmail status reports it.
    struct GmailAccount: Equatable {
        var connected: Bool
        var lastSync: Date?
    }

    /// Google covers mail: some account is connected and synced recently.
    static func gmailCovers(_ accounts: [GmailAccount], now: Date) -> Bool {
        accounts.contains { account in
            guard account.connected, let last = account.lastSync else { return false }
            return now.timeIntervalSince(last) < gmailFreshFor
        }
    }

    /// Some listed Google account isn't connected and synced recently, so Google can't vouch for
    /// its mail, even when another account is healthy.
    static func gmailLeavesUnchecked(_ accounts: [GmailAccount], now: Date) -> Bool {
        accounts.contains { account in
            guard account.connected, let last = account.lastSync else { return true }
            return now.timeIntervalSince(last) >= gmailFreshFor
        }
    }

    enum Route: Equatable {
        /// (a) script the open Mail.
        case mailApp
        /// (b) read the Envelope Index.
        case envelopeIndex
        /// (c) open Mail hidden and read it.
        case openMail
        /// Nothing more to do: today's behaviour (report why mail can't be read).
        case none(Skip)
    }

    enum Skip: Equatable {
        case gmailCovers, automationNotAllowed, cacheFresh, openedRecently, userQuitRecently, mailMissing, offline
    }

    struct Facts: Equatable {
        var mailOpen: Bool
        /// The Envelope Index read just succeeded (Full Disk Access on and the index there).
        var indexRead: Bool
        /// macOS already allows Harbor to script Mail. Undecided or denied is false.
        var automationAllowed: Bool
        var gmailCovers: Bool
        /// A listed Google account is unchecked (gmailLeavesUnchecked).
        var gmailUnchecked: Bool = false
        var mailInstalled = true
        /// The last successful read of Mail's mail (either way); nil when none yet.
        var lastRead: Date?
        /// The last successful read through Mail.app itself; an index read doesn't count.
        var lastLiveRead: Date? = nil
        /// When Harbor last opened Mail itself.
        var lastOpenedByHarbor: Date?
        /// When the person last quit Mail themselves.
        var userQuitAt: Date?
        /// Now is outside the person's working hours (offHours); false when the hours are unknown.
        var offHours = false
        /// The Mac has a network path. Offline, Mail couldn't fetch, so it isn't opened.
        var online = true
    }

    /// Working hours as service/user_hours.py holds them: minutes after local midnight, `days` with
    /// Monday = 0, a window past midnight wraps (and counts for the day it started). Outside it is
    /// off-hours. An empty window (start == end) has no working time, so every minute is off-hours.
    static func offHours(start: Int, end: Int, days: Set<Int>, minute: Int, weekday: Int) -> Bool {
        if start == end { return true }
        if start < end { return !(start <= minute && minute < end && days.contains(weekday)) }
        if minute >= start { return !days.contains(weekday) }
        if minute < end { return !days.contains((weekday + 6) % 7) }
        return true
    }

    /// Without Automation, Mail's index stands for its fetch: wait at least this long after opening…
    static let indexMinWait: TimeInterval = 30
    /// …and until the index hasn't changed for this long.
    static let indexQuietFor: TimeInterval = 20

    static func indexSettled(openedAt: Date, lastChange: Date, now: Date) -> Bool {
        now.timeIntervalSince(openedAt) >= indexMinWait && now.timeIntervalSince(lastChange) >= indexQuietFor
    }

    /// A closed-index read that failed with no permission cause (a lock, a busy WAL, an open error)
    /// is retried after these delays before anything is reported.
    static let closedRetryDelays: [TimeInterval] = [5, 20]
    /// How long a good read keeps vouching for the mail (service/tools/email_tools.py
    /// _MAIL_LIVE_MAX_AGE).
    static let goodReadFreshFor: TimeInterval = 65 * 60
    /// Next read after a failure was kept quiet because a recent good read stands.
    static let keptReadRetryAfter: TimeInterval = 90

    /// Whether a failed closed read is posted as "couldn't read Mail". A failure with a cause the
    /// person can fix (kind not "") always is. A transient one (kind "") is held back while the last
    /// good read is recent, and during off-hours, where Harbor deliberately doesn't open Mail.
    static func reportsClosedFailure(kind: String, lastGoodRead: Date?, now: Date, offHours: Bool) -> Bool {
        if !kind.isEmpty { return true }
        if offHours { return false }
        guard let lastGoodRead else { return true }
        return now.timeIntervalSince(lastGoodRead) >= goodReadFreshFor || lastGoodRead > now
    }

    /// Mail being open is not a fetch: signed out or offline it never writes its index. A read of
    /// the on-disk index counts as live only if the index (or its -wal) was written after Mail
    /// launched. `launchDate` unknown: not vouched for.
    static func indexReadIsLive(launchDate: Date?, lastIndexChange: Date?) -> Bool {
        guard let launchDate, let lastIndexChange else { return false }
        return lastIndexChange >= launchDate
    }

    /// Harbor may treat a Mail it asked to open as its own (and later quit it) only if that process
    /// started at or after the moment Harbor asked and Mail wasn't running just before. A Mail the
    /// person launched in between is theirs.
    static func launchWasHarborsOwn(launchDate: Date?, askedAt: Date, wasRunningBefore: Bool) -> Bool {
        guard !wasRunningBefore, let launchDate else { return false }
        return launchDate >= askedAt
    }

    /// Mail has finished fetching: its background activity count read 0 for the last
    /// fetchIdlePolls polls (newest last). An unreadable poll (nil) isn't idle.
    static func fetchSettled(_ polls: [Int?]) -> Bool {
        polls.count >= fetchIdlePolls && polls.suffix(fetchIdlePolls).allSatisfy { $0 == 0 }
    }

    /// Stop waiting: settled, or Mail hasn't answered the count the first three times (an older Mail
    /// or a busy one), so waiting the full fetchTimeout would only delay the read.
    static func fetchWaitOver(_ polls: [Int?]) -> Bool {
        fetchSettled(polls) || (polls.count >= 3 && polls.allSatisfy { $0 == nil })
    }

    /// The order: the open app first, then (only when allowed and polite) opening Mail, after the index
    /// read or in its place. Any route but .openMail leaves the index read, if any, as the result.
    static func route(_ f: Facts, now: Date) -> Route {
        if f.mailOpen && f.automationAllowed { return .mailApp }
        // Google covering mail: the index (when read) is all Mail needs to add.
        if f.gmailCovers && !f.gmailUnchecked { return f.indexRead ? .envelopeIndex : .none(.gmailCovers) }
        // Opening Mail needs no permission; reading it needs Automation or the index (Full Disk Access).
        if !f.automationAllowed && !f.indexRead { return .none(.automationNotAllowed) }
        if !f.mailInstalled { return .none(.mailMissing) }
        // The index is never fresh on its own (Mail fetches only while it runs), so with an index
        // read, or an unchecked Google account, only a live read counts.
        if !f.online { return .none(.offline) }
        let last = f.indexRead || f.gmailUnchecked ? f.lastLiveRead : f.lastRead
        let every = f.offHours ? offHoursOpenEvery : openEvery
        if let last, now.timeIntervalSince(last) < max(staleAfter, every) { return .none(.cacheFresh) }
        if let opened = f.lastOpenedByHarbor, now.timeIntervalSince(opened) < every { return .none(.openedRecently) }
        if let quit = f.userQuitAt, now.timeIntervalSince(quit) < userQuitGrace { return .none(.userQuitRecently) }
        return .openMail
    }

    /// Whether to quit Mail once the reads are done: only one Harbor opened, still hidden and in the
    /// background, that the person hasn't brought forward and that shows no window.
    static func quitsAfterReading(openedByHarbor: Bool, isHidden: Bool, isActive: Bool,
                                  personTouched: Bool, visibleWindows: Int) -> Bool {
        openedByHarbor && isHidden && !isActive && !personTouched && visibleWindows == 0
    }

    /// A Mail that quit counts as the person's quit unless Harbor asked it to.
    static func quitWasPerson(harborAskedToQuit: Bool) -> Bool { !harborAskedToQuit }
}

/// What macOS's Automation check means for Mail. `AEDeterminePermissionToAutomateTarget`
/// answers procNotFound (-600), not "allowed", while Mail isn't running, so a closed Mail would
/// always read as "not allowed" and a person who allowed Automation without Full Disk Access
/// would never get Mail opened and read. The last definite answer stands in for that case.
enum MailAutomation {
    static let rememberedKey = "harbor.mail.automationAllowed"
    static let procNotFound: Int32 = -600
    static let notPermitted: Int32 = -1743
    static let needsConsent: Int32 = -1744

    /// (allowed now, the answer to remember or nil to keep the remembered one).
    static func verdict(status: Int32, remembered: Bool?) -> (allowed: Bool, remember: Bool?) {
        switch status {
        case 0: return (true, true)
        case notPermitted, needsConsent: return (false, false)
        default: return (remembered ?? false, nil)       // not running, or no answer this time
        }
    }

    /// While Mail starts up: keep waiting for an answer, try a script, or give up.
    enum Wait: Equatable { case script, wait, stop }
    static func whileStarting(status: Int32) -> Wait {
        switch status {
        case 0: return .script
        case notPermitted, needsConsent: return .stop
        default: return .wait
        }
    }
}

