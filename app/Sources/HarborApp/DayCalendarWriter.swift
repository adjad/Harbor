import Combine
import EventKit
import Foundation

// The EventKit half of Day's calendar sync (the rules are DayCalendarSync.swift).
// When the person has said yes, every plan Day reads is turned into what to create,
// move and remove, and applied to the calendar they chose. The Google account in
// macOS Internet Accounts shows up in EventKit, so a block written to its calendar
// reaches Google Calendar too: no Google API and no new sign-in.
//
// Rules:
// - IMPORTANT: off until the person says yes. Nothing is read or written while it is off.
// - IMPORTANT: it only ever changes an event carrying Harbor's marker (DayCalendarMarker),
//   re-checked on the event itself just before it is edited or removed.
// - IMPORTANT: the calendar is written with the full Calendar access setup already asks
//   for. Without it nothing is written and the Settings row says so; it never asks on
//   its own, only when the person turns the sync on.
// - IMPORTANT: one run at a time. A plan that arrives meanwhile is run right after.

extension TodayPlan {
    /// Day's blocks as the sync reads them.
    var calendarSyncInputs: [DayCalendarSync.Input] {
        let pinned = Dictionary(tasks.map { ($0.id, $0.pinned_start != nil) }, uniquingKeysWith: { first, _ in first })
        return displayBlocks.map { block in
            DayCalendarSync.Input(id: block.id, title: block.title, start: block.start, end: block.end,
                                  kind: block.kind, taskID: block.task_id, allDay: block.all_day == true,
                                  pinned: block.task_id.flatMap { pinned[$0] } ?? false,
                                  addedElsewhere: block.added == true)
        }
    }

    /// The stretch of this plan's day, in its own time zone.
    var calendarSyncScope: ClosedRange<Double>? {
        guard let zone = TimeZone(identifier: timezone) else { return nil }
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = zone
        let parts = day.split(separator: "-").compactMap { Int($0) }
        guard parts.count == 3,
              let first = calendar.date(from: DateComponents(year: parts[0], month: parts[1], day: parts[2])),
              let next = calendar.date(byAdding: .day, value: 1, to: first) else { return nil }
        return first.timeIntervalSince1970...next.timeIntervalSince1970
    }

    /// Whether the plan is whole enough to remove a block's event for being absent from it.
    var calendarSyncCanRemove: Bool { sources["calendar"]?.ready == true }
}

@MainActor
final class DayCalendarWriter {
    static let shared = DayCalendarWriter()

    /// What the last run could not do, in a sentence for Settings; nil when it went through.
    private(set) var problem: String?
    /// Called after Harbor changed the calendar, so Calendar is read again at once.
    var didWrite: (() -> Void)?

    private var lastPlan: TodayPlan?
    private var running = false
    private var again = false
    private var settingsObserver: NSObjectProtocol?

    private init() {}

    /// Follows the Day's plan, and the person's settings, for the life of the app.
    func attach(to model: TodayModel) -> AnyCancellable {
        if settingsObserver == nil {
            settingsObserver = NotificationCenter.default.addObserver(
                forName: DayCalendarSyncSettings.changed, object: nil, queue: .main) { [weak self] _ in
                Task { @MainActor in self?.start() }
            }
        }
        return model.$plan.compactMap { $0 }.receive(on: DispatchQueue.main).sink { [weak self] plan in
            self?.lastPlan = plan
            self?.start()
        }
    }

    private func start() {
        guard !running else { again = true; return }
        running = true
        Task { @MainActor in
            repeat {
                again = false
                await run()
            } while again
            running = false
        }
    }

    private func run() async {
        guard Self.fullAccess else {
            problem = DayCalendarSyncSettings.isEnabled() ? "Harbor needs full Calendar access to add to your calendar." : nil
            return
        }
        let enabled = DayCalendarSyncSettings.isEnabled()
        let chosen = DayCalendarSyncSettings.calendarID()
        let now = Date().timeIntervalSince1970
        guard enabled else {
            // Turned off: what Harbor added and that is still ahead goes, nothing else.
            let result = await Task.detached(priority: .utility) {
                Self.apply(.removeAll, calendarID: chosen, now: now)
            }.value
            problem = nil
            if result.changed { didWrite?() }
            return
        }
        guard let plan = lastPlan, let scope = plan.calendarSyncScope,
              scope.upperBound > now else { return }   // a day that is over is history
        let wanted = DayCalendarSync.wanted(from: plan.calendarSyncInputs)
        let canRemove = plan.calendarSyncCanRemove
        let result = await Task.detached(priority: .utility) {
            Self.apply(.sync(wanted: wanted, scope: scope, canRemove: canRemove), calendarID: chosen, now: now)
        }.value
        problem = result.problem
        if result.changed { didWrite?() }
    }

    // MARK: EventKit

    enum Job {
        case sync(wanted: [DayCalendarSync.Block], scope: ClosedRange<Double>, canRemove: Bool)
        case removeAll
    }

    struct Result: Sendable {
        var changed = false
        var problem: String?
    }

    nonisolated static var fullAccess: Bool { EKEventStore.authorizationStatus(for: .event) == .fullAccess }

    /// Reads Harbor's own events, plans, and applies the plan with its own event store.
    nonisolated static func apply(_ job: Job, calendarID: String?, now: Double) -> Result {
        guard fullAccess else { return Result(problem: "Harbor needs full Calendar access to add to your calendar.") }
        let store = EKEventStore()
        let from = Calendar.current.startOfDay(for: Date(timeIntervalSince1970: now)).addingTimeInterval(-86400)
        let until = from.addingTimeInterval(60 * 86400)
        let events = store.events(matching: store.predicateForEvents(withStart: from, end: until, calendars: nil))
        let existing: [DayCalendarSync.Existing] = events.compactMap { event in
            guard let blockID = DayCalendarMarker.blockID(url: event.url?.absoluteString, notes: event.notes),
                  let id = event.eventIdentifier, let start = event.startDate, let end = event.endDate else { return nil }
            return DayCalendarSync.Existing(eventID: id, blockID: blockID, title: event.title ?? "",
                                            start: start.timeIntervalSince1970, end: end.timeIntervalSince1970,
                                            calendarID: event.calendar?.calendarIdentifier ?? "")
        }
        let target: EKCalendar?
        if let calendarID, let chosen = store.calendar(withIdentifier: calendarID), chosen.allowsContentModifications {
            target = chosen
        } else if let fallback = store.defaultCalendarForNewEvents, fallback.allowsContentModifications {
            target = fallback
        } else {
            target = nil
        }
        let plan: DayCalendarSync.Plan
        switch job {
        case .removeAll:
            plan = DayCalendarSync.planRemovingAll(existing: existing, now: now)
        case .sync(let wanted, let scope, let canRemove):
            guard let target else {
                return Result(problem: "The calendar you chose can't be changed. Pick another one in Settings.")
            }
            plan = DayCalendarSync.plan(wanted: wanted, existing: existing, scope: scope, canRemove: canRemove,
                                        calendarID: target.calendarIdentifier, now: now)
        }
        guard !plan.isEmpty else { return Result() }

        var result = Result()
        var failures = 0
        func isOwn(_ event: EKEvent) -> Bool {
            DayCalendarMarker.isOwn(url: event.url?.absoluteString, notes: event.notes)
        }
        for block in plan.creates {
            guard let target else { continue }
            let event = EKEvent(eventStore: store)
            fill(event, from: block, calendar: target)
            do { try store.save(event, span: .thisEvent, commit: false); result.changed = true } catch { failures += 1 }
        }
        for update in plan.updates {
            // Looked up again and checked for the marker: never an event that isn't Harbor's.
            guard let event = store.event(withIdentifier: update.eventID), isOwn(event) else { continue }
            fill(event, from: update.block, calendar: update.moveCalendar ? target : nil)
            do { try store.save(event, span: .thisEvent, commit: false); result.changed = true } catch { failures += 1 }
        }
        for eventID in plan.deletes {
            guard let event = store.event(withIdentifier: eventID), isOwn(event) else { continue }
            do { try store.remove(event, span: .thisEvent, commit: false); result.changed = true } catch { failures += 1 }
        }
        do { try store.commit() } catch { failures += 1; store.reset() }
        if failures > 0 { result.problem = "Some of Harbor's blocks could not be added to your calendar." }
        return result
    }

    nonisolated private static func fill(_ event: EKEvent, from block: DayCalendarSync.Block, calendar: EKCalendar?) {
        event.title = block.title
        event.startDate = Date(timeIntervalSince1970: block.start)
        event.endDate = Date(timeIntervalSince1970: block.end)
        event.isAllDay = false
        event.url = URL(string: DayCalendarMarker.url(forBlock: block.id))
        event.notes = DayCalendarMarker.notes(forBlock: block.id, keeping: event.notes)
        if let calendar { event.calendar = calendar }
    }

    // MARK: What the setup card and Settings need

    /// Every calendar this Mac has, for the picker. Reads only; nothing here asks for access.
    nonisolated static func choices() -> [DayCalendarChoice] {
        guard fullAccess else { return [] }
        let store = EKEventStore()
        let fallback = store.defaultCalendarForNewEvents?.calendarIdentifier
        return store.calendars(for: .event).map { calendar in
            DayCalendarChoice(id: calendar.calendarIdentifier, title: calendar.title,
                              account: calendar.source?.title ?? "",
                              writable: calendar.allowsContentModifications,
                              isDefault: calendar.calendarIdentifier == fallback)
        }
    }

    /// Full access to write, asked the moment the person says yes (setup asks for it too,
    /// so usually this is already true). True when Harbor may write; false when macOS has
    /// the answer at "no" or "add only", which only System Settings can change.
    static func ensureFullAccess() async -> Bool {
        if fullAccess { return true }
        guard EKEventStore.authorizationStatus(for: .event) == .notDetermined else { return false }
        return (try? await EKEventStore().requestFullAccessToEvents()) ?? false
    }
}
