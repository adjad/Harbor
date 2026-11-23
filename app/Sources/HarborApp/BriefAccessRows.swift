import AppKit
import SwiftUI

/// The one setup row Brief (and, before onboarding is done, Day) can show. Setup asks
/// for Calendar and Reminders; the rest is asked at the moment a task needs it, and
/// lives in Settings. Until onboarding is finished, every source Harbor can't read yet
/// is folded into ONE row, "Finish setup · 3", that opens the notch permission page
/// (the whole list). It names no source twice, has no per-source Allow button, and is
/// gone once onboarding is finished (`onboardingDone`).
///
/// What counts as missing:
///   - Mail & Notes Automation, undecided while Mail is on this Mac, Google isn't
///     covering mail and the Envelope Index can't be read (mail);
///   - Full Disk Access, when Messages' history can't be opened (texts), or Mail and
///     Notes while those apps are closed and their stores can't be opened;
///   - the plan's own unread Calendar, Reminders, mail and texts, when the service named
///     a permission as the reason, and Contacts when macOS turned it down.
/// A source is never missing when the live probe (AccessProbe) says that store reads, or
/// can't say.
///
/// After onboarding Day keeps one quiet line, only when Calendar or Reminders (its own
/// data) can't be read; Brief keeps none. `decide`, `finishRow` and `dayLine` are pure
/// (tests/FirstRunSetupChecks.swift); the model reads the facts, the view draws them.
enum BriefAccessRows {
    /// A source that can be missing, counted once however many places report it.
    enum Source: String, CaseIterable, Equatable {
        case calendar, reminders, mail, texts, notes, contacts
    }

    /// What the rows are decided from.
    struct Facts: Equatable {
        /// Mail & Notes Automation has no answer yet: not allowed, not refused.
        var automationUndecided = false
        /// A connected Gmail account that synced recently (MailRecache.gmailCovers).
        var gmailCovers = false
        var mailInstalled = true
        var probe: AccessProbe.State = .unknown
        var outcomes: [AccessProbe.Store: AccessProbe.Outcome] = [:]
        var mailOpen = false
        var notesOpen = false
    }

    struct Shown: Equatable {
        var allowMail = false
        var seeTexts = false
        /// Mail and/or Notes, closed and unreadable, in the order Mail, Notes.
        var closed: [AccessProbe.Store] = []

        /// The sources these facts say are missing.
        var sources: Set<Source> {
            var out: Set<Source> = []
            if allowMail { out.insert(.mail) }
            if seeTexts { out.insert(.texts) }
            for store in closed { out.insert(store == .mail ? .mail : .notes) }
            return out
        }
        var isEmpty: Bool { sources.isEmpty }
    }

    /// The stores macOS refused, from one probe. `.on` and `.unknown` refuse none.
    /// `.partial` names them; `.off` is every store that exists, which the outcomes say
    /// (all of them when they are missing). Pure.
    static func unreadable(_ state: AccessProbe.State, _ outcomes: [AccessProbe.Store: AccessProbe.Outcome]) -> Set<AccessProbe.Store> {
        switch state {
        case .on, .unknown: return []
        case .partial(let stores): return Set(stores)
        case .off:
            let refused = outcomes.filter { $0.value == .refused }.map(\.key)
            return refused.isEmpty ? Set(AccessProbe.Store.allCases) : Set(refused)
        }
    }

    /// Which sources the probe and Automation say are missing right now. Pure.
    static func decide(_ f: Facts) -> Shown {
        var shown = Shown()
        let indexReadable = f.outcomes[.mail] == .readable
        shown.allowMail = f.automationUndecided && !f.gmailCovers && !indexReadable && f.mailInstalled
        let unread = unreadable(f.probe, f.outcomes)
        shown.seeTexts = unread.contains(.messages)
        if unread.contains(.mail), !f.mailOpen, !f.gmailCovers { shown.closed.append(.mail) }
        if unread.contains(.notes), !f.notesOpen { shown.closed.append(.notes) }
        return shown
    }

    // MARK: The plan's own unread sources

    /// Reasons that a person fixes with a permission (SourceAccess.blocked). Anything else
    /// (no accounts, a Google sign-in, a transient read) is not a setup row.
    static let permissionBlocks: Set<String> = [
        "calendar_access", "reminders_access", "full_disk_access", "mail_automation",
        "mail_closed", "notes_closed", "contacts_access",
    ]

    /// The missing sources among the plan's unread ones, by the permission the service named.
    /// Pure.
    static func missing(_ unread: [SourceAccess]) -> Set<Source> {
        var out: Set<Source> = []
        for access in unread where !access.readable && access.state == .unavailable {
            guard let blocked = access.blocked, permissionBlocks.contains(blocked) else { continue }
            switch access.source {
            case "calendar": out.insert(.calendar)
            case "reminders": out.insert(.reminders)
            case "email", "mail": out.insert(.mail)
            case "messages", "texts": out.insert(.texts)
            case "notes": out.insert(.notes)
            case "contacts": out.insert(.contacts)
            default: break
            }
        }
        return out
    }

    // MARK: The row

    /// The one row: how many distinct sources are still missing.
    struct Finish: Equatable {
        let sources: [Source]
        var count: Int { sources.count }
        var title: String { "Finish setup · \(count)" }
    }

    static let finishHelp = "Harbor can't see some of your things yet. Open the permissions page to turn them on."
    static let finishSymbol = "lock.shield"

    /// Onboarding is finished (or never started, as for someone who upgraded): the flag
    /// FirstRunSetup keeps (`harbor.firstRun.done`, with `harbor.firstRun.started`). Pure.
    static func onboardingDone(_ flag: (String) -> Bool = { UserDefaults.standard.bool(forKey: $0) }) -> Bool {
        !FirstRunSetup.isUnfinished(flag)
    }

    /// The Brief's row: nil once onboarding is done, and nil when nothing is missing; else
    /// one row naming each missing source once. Pure.
    static func finishRow(_ shown: Shown, planMissing: Set<Source>, onboardingDone: Bool) -> Finish? {
        guard !onboardingDone else { return nil }
        let all = shown.sources.union(planMissing)
        guard !all.isEmpty else { return nil }
        return Finish(sources: Source.allCases.filter(all.contains))
    }

    /// Day after onboarding: one quiet line only for Calendar or Reminders, Day's own
    /// data; nothing else is Day's to say. Before onboarding is done the Finish setup row
    /// speaks for everything, so Day adds no line. Pure.
    static func dayLine(planMissing: Set<Source>, onboardingDone: Bool) -> [Source] {
        guard onboardingDone else { return [] }
        return [Source.calendar, .reminders].filter(planMissing.contains)
    }

    /// The row's button: the notch permission page, the whole list.
    @MainActor static func openPermissionPage() {
        NotificationCenter.default.post(name: .harborOpenPermissionPage, object: nil)
    }
}

/// Reads the facts. Nothing here asks macOS: the row only opens the permission page.
@MainActor
final class BriefAccessRowsModel: ObservableObject {
    static let shared = BriefAccessRowsModel()

    @Published private(set) var shown = BriefAccessRows.Shown()
    private var live = true
    private var generation = 0

    init() {}

    #if HARBOR_NOTCH_QA
    /// A fixed set of facts for a render: reads nothing from this Mac.
    static func qaFixture(_ shown: BriefAccessRows.Shown) -> BriefAccessRowsModel {
        let model = BriefAccessRowsModel()
        model.live = false
        model.shown = shown
        return model
    }
    #endif

    /// Whether the row may show at all (a render's fixture shows it).
    var onboardingDone: Bool { live ? BriefAccessRows.onboardingDone() : false }

    /// Reads the facts again. Cheap: the probe's answer is already kept, the Automation
    /// read is `ask: false`, and Gmail is checked only when it could change the answer.
    /// Does nothing once onboarding is done: no row will show.
    func refresh(now: Date = Date()) async {
        guard live, !BriefAccessRows.onboardingDone() else { return }
        generation += 1
        let mine = generation
        let monitor = AccessProbeMonitor.shared
        var facts = BriefAccessRows.Facts()
        facts.probe = monitor.state
        facts.outcomes = monitor.reading.outcomes
        facts.mailOpen = !NSRunningApplication.runningApplications(withBundleIdentifier: "com.apple.mail").isEmpty
        facts.notesOpen = !NSRunningApplication.runningApplications(withBundleIdentifier: "com.apple.Notes").isEmpty
        facts.mailInstalled = NSWorkspace.shared.urlForApplication(withBundleIdentifier: "com.apple.mail") != nil
        // Mail & Notes Automation: .notAsked is "no answer yet". Reads only.
        let automation = await Task.detached { FirstRunPermissions.automationStateNow(ask: false) }.value
        facts.automationUndecided = automation == .notAsked
        // Google only matters when it would hide a mail entry.
        let mailMatters = facts.automationUndecided || BriefAccessRows.unreadable(facts.probe, facts.outcomes).contains(.mail)
        if mailMatters {
            facts.gmailCovers = MailRecache.gmailCovers(await Self.gmailAccounts(), now: now)
        }
        guard mine == generation else { return }
        let next = BriefAccessRows.decide(facts)
        if next != shown { shown = next }
    }

    private static func gmailAccounts() async -> [MailRecache.GmailAccount] {
        var req = URLRequest(url: HarborClient.baseURL.appendingPathComponent("assistant/gmail/status"))
        req.timeoutInterval = 5
        guard let (data, response) = try? await URLSession.shared.data(for: req),
              (response as? HTTPURLResponse)?.statusCode == 200,
              let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return [] }
        let rows = json["accounts"] as? [[String: Any]] ?? []
        return rows.compactMap(GoogleStatus.Account.parse).map {
            MailRecache.GmailAccount(connected: $0.state == .connected, lastSync: $0.lastSync)
        }
    }
}

/// The one row, in Brief's and Day's access-row style. `planMissing` is what the plan
/// says it can't read (BriefAccessRows.missing), so a source named there and by the
/// probe is counted once.
struct BriefAccessRowsView: View {
    @ObservedObject private var model: BriefAccessRowsModel
    @ObservedObject private var probe = AccessProbeMonitor.shared
    private let planMissing: Set<BriefAccessRows.Source>

    init(model: BriefAccessRowsModel? = nil, planMissing: Set<BriefAccessRows.Source> = []) {
        self.model = model ?? BriefAccessRowsModel.shared
        self.planMissing = planMissing
    }

    var body: some View {
        Group {
            if let row = BriefAccessRows.finishRow(model.shown, planMissing: planMissing, onboardingDone: model.onboardingDone) {
                line(row)
            }
        }
        .task { await model.refresh() }
        .onChange(of: probe.reading) { _, _ in Task { await model.refresh() } }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            Task { await model.refresh() }
        }
    }

    private func line(_ row: BriefAccessRows.Finish) -> some View {
        Button { BriefAccessRows.openPermissionPage() } label: {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Image(systemName: BriefAccessRows.finishSymbol).font(.harbor(size: 10.5))
                    .foregroundStyle(Theme.textMuted).frame(width: 15)
                Text(row.title).font(.harbor(size: 12.5)).foregroundStyle(Theme.textSecondary)
                    .lineLimit(1).truncationMode(.tail)
                Spacer(minLength: 6)
                Image(systemName: "chevron.right").font(.harbor(size: 9, weight: .medium)).foregroundStyle(Theme.textMuted)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help(BriefAccessRows.finishHelp)
        .accessibilityLabel(row.title)
        .accessibilityHint("Opens the permissions page")
    }
}
