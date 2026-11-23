import Foundation

// Day writes the blocks Harbor plans for the person into a calendar they pick. This
// file is the pure half: which of Day's blocks are Harbor's own to write, what to
// create, move or remove given the events Harbor already wrote, the person's two
// settings, and how the calendar picker groups its choices. No EventKit and no
// network, so tests/DayCalendarSyncChecks.swift compiles it alone. The EventKit half is
// DayCalendarWriter.swift; the marker that tells Harbor's events from the person's is
// DayCalendarMarker (CalendarReader.swift).
//
// Rules:
// - IMPORTANT: only Harbor's own blocks are written: a study block, and a block that
//   comes from a task the person kept or Harbor holds. Never an event already on the
//   calendar, never an unaccepted suggestion, never time Harbor only guesses is busy.
// - IMPORTANT: the plan only ever names events carrying Harbor's marker. A person's own
//   event is not in `existing`, so it can never be updated or removed.
// - IMPORTANT: idempotent. A block with an event is updated in place (matched by block
//   id), never created again; a second event for one block is removed.
// - IMPORTANT: an event that ended before now is history. It is never moved or removed.
// - IMPORTANT: a block Harbor keeps re-planning around the clock ("floating") does not
//   move its event for a small shift, and is not written while it starts within minutes.

enum DayCalendarSync {
    /// A block as Day's plan hands it over.
    struct Input: Equatable {
        var id: String
        var title: String
        var start: Double
        var end: Double?
        /// The planner's kind: "study", "project", "task" (kept work), "suggestion", "calendar", …
        var kind: String
        var taskID: String? = nil
        var allDay = false
        /// The task has a fixed start the person (or a kept suggestion) set. Study blocks have none.
        var pinned = false
        /// The service already booked this study block on the calendar (the older
        /// "Add to calendar" button); that event stands, so this sync adds none.
        var addedElsewhere = false
    }

    /// One block to keep on the calendar.
    struct Block: Equatable {
        var id: String
        var title: String
        var start: Double
        var end: Double
        /// Re-planned from the clock: its start can drift.
        var floating: Bool
    }

    /// An event Harbor wrote earlier (its marker named `blockID`).
    struct Existing: Equatable {
        var eventID: String
        var blockID: String
        var title: String
        var start: Double
        var end: Double
        var calendarID: String
    }

    struct Update: Equatable {
        var eventID: String
        var block: Block
        /// Also moves the event to the chosen calendar.
        var moveCalendar: Bool
    }

    struct Plan: Equatable {
        var creates: [Block] = []
        var updates: [Update] = []
        /// Event ids of Harbor's own events to remove.
        var deletes: [String] = []
        var isEmpty: Bool { creates.isEmpty && updates.isEmpty && deletes.isEmpty }
    }

    /// A floating block that starts within this long is not written yet: its start moves with the clock.
    static let floatingLead: Double = 15 * 60
    /// A floating block's event keeps its time through a shift this small.
    static let floatingTolerance: Double = 5 * 60
    /// Any other block's event is moved for a shift of a minute or more.
    static let exactTolerance: Double = 60
    /// Study blocks carry this id prefix (service/study/blocks_api.py).
    static let studyPrefix = "study-block:"
    static let titleLimit = 200

    // MARK: Which blocks are Harbor's own

    /// The blocks to keep on the calendar, from everything Day shows. Duplicate ids keep the first.
    static func wanted(from inputs: [Input]) -> [Block] {
        var seen = Set<String>()
        var out: [Block] = []
        for input in inputs {
            guard let block = block(from: input), seen.insert(block.id).inserted else { continue }
            out.append(block)
        }
        return out
    }

    static func block(from input: Input) -> Block? {
        guard !input.allDay, !input.id.isEmpty, input.start.isFinite,
              let end = input.end, end.isFinite, end > input.start, !input.addedElsewhere else { return nil }
        let study = input.kind == "study" && input.id.hasPrefix(studyPrefix)
        let kept = input.taskID != nil && ["study", "project", "task"].contains(input.kind)
        guard study || kept else { return nil }
        let title = input.title.split(whereSeparator: \.isNewline).joined(separator: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !title.isEmpty else { return nil }
        return Block(id: input.id, title: String(title.prefix(titleLimit)), start: input.start, end: end,
                     floating: study || !input.pinned)
    }

    // MARK: What to do on the calendar

    /// What to create, move and remove.
    /// - Parameters:
    ///   - wanted: the blocks of the plan being synced (`wanted(from:)`).
    ///   - existing: Harbor's own events in the calendar (marker found), over the whole range read.
    ///   - scope: the stretch the plan covers (its day). A block of Harbor's that is no longer in the
    ///     plan is removed only when its event lies in the scope, and only with `canRemove`.
    ///   - canRemove: false while the plan can't be trusted to be whole (Calendar not current).
    ///   - calendarID: the calendar the person chose.
    static func plan(wanted: [Block], existing: [Existing], scope: ClosedRange<Double>?, canRemove: Bool,
                     calendarID: String, now: Double) -> Plan {
        var plan = Plan()
        var byBlock: [String: [Existing]] = [:]
        for event in existing where !event.blockID.isEmpty { byBlock[event.blockID, default: []].append(event) }
        let wantedIDs = Set(wanted.map(\.id))

        for block in wanted {
            let events = (byBlock[block.id] ?? []).sorted {
                ($0.calendarID == calendarID ? 0 : 1, $0.eventID) < ($1.calendarID == calendarID ? 0 : 1, $1.eventID)
            }
            guard let keep = events.first else {
                if block.end <= now { continue }
                if block.floating && block.start < now + floatingLead { continue }
                plan.creates.append(block)
                continue
            }
            // A second event for the same block is a duplicate: it goes, unless it is history.
            for extra in events.dropFirst() where extra.end > now { plan.deletes.append(extra.eventID) }
            if keep.end <= now { continue }
            let tolerance = block.floating ? floatingTolerance : exactTolerance
            let moved = abs(keep.start - block.start) >= tolerance
                || (!block.floating && abs(keep.end - block.end) >= exactTolerance)
                || (block.floating && abs((keep.end - keep.start) - (block.end - block.start)) >= tolerance)
            let renamed = keep.title != block.title
            let elsewhere = keep.calendarID != calendarID
            guard moved || renamed || elsewhere else { continue }
            // A floating block that only drifted keeps the time its event has.
            var next = block
            if block.floating && !moved {
                next.start = keep.start
                next.end = keep.end
            }
            plan.updates.append(Update(eventID: keep.eventID, block: next, moveCalendar: elsewhere))
        }

        guard canRemove, let scope else { return plan }
        for event in existing where !wantedIDs.contains(event.blockID) && !event.blockID.isEmpty {
            guard event.end > now, event.start <= scope.upperBound, event.end >= scope.lowerBound else { continue }
            plan.deletes.append(event.eventID)
        }
        return plan
    }

    /// Everything of Harbor's still ahead, to remove when the person turns the sync off.
    static func planRemovingAll(existing: [Existing], now: Double) -> Plan {
        Plan(creates: [], updates: [], deletes: existing.filter { $0.end > now && !$0.blockID.isEmpty }.map(\.eventID))
    }
}

// MARK: - The person's two choices

/// Whether Day writes to the calendar, and which one. Off until the person says yes.
enum DayCalendarSyncSettings {
    static let enabledKey = "dayCalendarSync.enabled"
    static let calendarKey = "dayCalendarSync.calendarID"
    static let changed = Notification.Name("harborDayCalendarSyncSettingsChanged")

    static func isEnabled(_ defaults: UserDefaults = .standard) -> Bool { defaults.bool(forKey: enabledKey) }

    /// The chosen calendar's id; nil until one is chosen (the default calendar is used).
    static func calendarID(_ defaults: UserDefaults = .standard) -> String? {
        let id = defaults.string(forKey: calendarKey)
        return (id?.isEmpty ?? true) ? nil : id
    }

    static func set(enabled: Bool, calendarID: String?, _ defaults: UserDefaults = .standard) {
        defaults.set(enabled, forKey: enabledKey)
        if let calendarID, !calendarID.isEmpty { defaults.set(calendarID, forKey: calendarKey) }
        NotificationCenter.default.post(name: changed, object: nil)
    }
}

/// One calendar the picker offers.
struct DayCalendarChoice: Equatable, Identifiable {
    var id: String
    var title: String
    /// The account it belongs to ("iCloud", a Google address, "On My Mac").
    var account: String
    var writable: Bool
    /// The calendar macOS creates new events in.
    var isDefault: Bool
}

enum DayCalendarChoices {
    struct Group: Equatable, Identifiable {
        var account: String
        var choices: [DayCalendarChoice]
        var id: String { account }
    }

    /// Writable calendars only, grouped by account (accounts and calendars by name).
    static func groups(_ all: [DayCalendarChoice]) -> [Group] {
        let byAccount = Dictionary(grouping: all.filter(\.writable)) { $0.account.isEmpty ? "Other" : $0.account }
        return byAccount.keys.sorted { $0.localizedCaseInsensitiveCompare($1) == .orderedAscending }.map { account in
            Group(account: account, choices: (byAccount[account] ?? []).sorted {
                $0.title.localizedCaseInsensitiveCompare($1.title) == .orderedAscending })
        }
    }

    /// The calendar chosen when the picker opens: the stored one if it is still there and
    /// writable, else the person's default calendar, else the first writable one.
    static func selection(_ all: [DayCalendarChoice], stored: String?) -> String? {
        let writable = all.filter(\.writable)
        if let stored, writable.contains(where: { $0.id == stored }) { return stored }
        return (writable.first(where: \.isDefault) ?? groups(all).first?.choices.first)?.id
    }
}
