// Day's calendar sync, pure rules only: which of Day's blocks are Harbor's own to write,
// what to create, move and remove given the events Harbor already wrote, the calendar
// picker, and the setup card's copy and decisions. No EventKit, no Calendar access and
// no network. Compile with DayCalendarSync.swift and OnboardingCalendarSync.swift.
// Invented names only.
import Foundation

@main struct DayCalendarSyncChecks {
    typealias S = DayCalendarSync
    static let now = 1_000_000.0
    static let hour = 3600.0

    static func main() {
        wantedChecks()
        planChecks()
        removalChecks()
        floatingChecks()
        settingsAndPickerChecks()
        cardChecks()
        print("synthetic day calendar sync checks passed")
    }

    static func check(_ ok: Bool, _ message: String) { precondition(ok, message) }

    static func input(_ id: String, _ title: String = "Reading: STAT 101", start: Double = now + 2 * hour,
                      end: Double? = now + 3 * hour, kind: String = "study", task: String? = nil,
                      pinned: Bool = true, added: Bool = false, allDay: Bool = false) -> S.Input {
        S.Input(id: id, title: title, start: start, end: end, kind: kind, taskID: task, allDay: allDay,
                pinned: pinned, addedElsewhere: added)
    }

    static func block(_ id: String, _ title: String = "Reading: STAT 101", start: Double = now + 2 * hour,
                      end: Double = now + 3 * hour, floating: Bool = false) -> S.Block {
        S.Block(id: id, title: title, start: start, end: end, floating: floating)
    }

    static func event(_ id: String, block: String, _ title: String = "Reading: STAT 101", start: Double = now + 2 * hour,
                      end: Double = now + 3 * hour, calendar: String = "cal-1") -> S.Existing {
        S.Existing(eventID: id, blockID: block, title: title, start: start, end: end, calendarID: calendar)
    }

    // Only Harbor's own blocks: study blocks and kept work. Nothing the person already has.
    static func wantedChecks() {
        let inputs = [
            input("study-block:a1"),
            input("task:t1", "Homework 3", kind: "task", task: "t1"),
            input("task:t2", "Project outline", kind: "project", task: "t2"),
            input("cal:9", "Dentist", kind: "calendar"),
            input("manual:4", "Lunch with Priya", kind: "manual"),
            input("daily:x", "Review: 3 emails", kind: "suggestion"),
            input("behavior:p", "Likely busy", kind: "behavior"),
            input("behavior-away:p", "Possible time away", kind: "behavior_away"),
            input("study-block:booked", added: true),
            input("study-block:untimed", end: nil),
            input("task:t3", "Nap", kind: "task", task: "t3", allDay: true),
            input("task:t4", "  ", kind: "task", task: "t4"),
            input("task:t1", "Duplicate id", kind: "task", task: "t1"),
            input("task:t5", "Backwards", start: now + 3 * hour, end: now + 2 * hour, kind: "task", task: "t5"),
        ]
        let ids = S.wanted(from: inputs).map(\.id)
        check(ids == ["study-block:a1", "task:t1", "task:t2"], "only Harbor's own blocks, once each: \(ids)")
        check(S.wanted(from: inputs)[1].title == "Homework 3", "the first of a repeated id is kept")
        check(S.wanted(from: [input("task:l", "Line one\nline two", kind: "task", task: "l")]).first?.title == "Line one line two",
              "a title is one line")
        check(S.wanted(from: [input("task:f", kind: "task", task: "f", pinned: false)]).first?.floating == true,
              "an unpinned task floats")
        check(S.wanted(from: [input("study-block:f")]).first?.floating == true, "a study block floats")
        check(S.wanted(from: [input("task:p", kind: "task", task: "p", pinned: true)]).first?.floating == false,
              "a pinned task is exact")
    }

    // Create what is missing, update what moved, never duplicate.
    static func planChecks() {
        let a = block("study-block:a1"), b = block("task:t1", "Homework 3", start: now + 4 * hour, end: now + 5 * hour)
        let first = S.plan(wanted: [a, b], existing: [], scope: nil, canRemove: true, calendarID: "cal-1", now: now)
        check(first.creates == [a, b] && first.updates.isEmpty && first.deletes.isEmpty, "everything new is created")

        // Re-syncing what is already written does nothing: idempotent.
        let written = [event("e1", block: "study-block:a1"),
                       event("e2", block: "task:t1", "Homework 3", start: now + 4 * hour, end: now + 5 * hour)]
        check(S.plan(wanted: [a, b], existing: written, scope: nil, canRemove: true, calendarID: "cal-1", now: now).isEmpty,
              "a second sync changes nothing")

        // Moved (Replan, drag): the same event is updated, not a new one created.
        let moved = block("task:t1", "Homework 3", start: now + 6 * hour, end: now + 7 * hour)
        let move = S.plan(wanted: [a, moved], existing: written, scope: nil, canRemove: true, calendarID: "cal-1", now: now)
        check(move.creates.isEmpty && move.deletes.isEmpty, "a move creates and removes nothing")
        check(move.updates == [S.Update(eventID: "e2", block: moved, moveCalendar: false)], "a move updates its event")

        // Renamed, and moved to another calendar.
        let renamed = block("study-block:a1", "Reading: STAT 101 ch. 4")
        let rename = S.plan(wanted: [renamed], existing: [written[0]], scope: nil, canRemove: true, calendarID: "cal-1", now: now)
        check(rename.updates.first?.block.title == "Reading: STAT 101 ch. 4", "a new title updates the event")
        let elsewhere = S.plan(wanted: [a], existing: [written[0]], scope: nil, canRemove: true, calendarID: "cal-2", now: now)
        check(elsewhere.updates == [S.Update(eventID: "e1", block: a, moveCalendar: true)], "another calendar moves the event")

        // A duplicate event for one block is removed, the one in the chosen calendar stays.
        let twins = [event("e9", block: "study-block:a1", calendar: "cal-2"), event("e1", block: "study-block:a1")]
        let dedupe = S.plan(wanted: [a], existing: twins, scope: nil, canRemove: true, calendarID: "cal-1", now: now)
        check(dedupe.deletes == ["e9"] && dedupe.creates.isEmpty && dedupe.updates.isEmpty, "duplicates are removed: \(dedupe)")

        // A block that is already over is not written; one still ahead is.
        let over = block("task:old", "Done already", start: now - 3 * hour, end: now - 2 * hour)
        check(S.plan(wanted: [over], existing: [], scope: nil, canRemove: true, calendarID: "cal-1", now: now).creates.isEmpty,
              "a block already over is not written")
    }

    // Removed, cancelled or done: its event goes. Only Harbor's, only ahead, only in scope.
    static func removalChecks() {
        let day = (now - 6 * hour)...(now + 18 * hour)
        let ahead = event("e1", block: "task:gone")
        let later = event("e2", block: "task:nextweek", start: now + 200 * hour, end: now + 201 * hour)
        let history = event("e3", block: "task:earlier", start: now - 5 * hour, end: now - 4 * hour)
        let kept = event("e4", block: "study-block:a1")
        let plan = S.plan(wanted: [block("study-block:a1")], existing: [ahead, later, history, kept],
                          scope: day, canRemove: true, calendarID: "cal-1", now: now)
        check(plan.deletes == ["e1"], "only the vanished block inside the day is removed: \(plan.deletes)")
        check(plan.creates.isEmpty && plan.updates.isEmpty, "nothing else changes")

        let blocked = S.plan(wanted: [], existing: [ahead], scope: day, canRemove: false, calendarID: "cal-1", now: now)
        check(blocked.deletes.isEmpty, "nothing is removed while the plan is not whole")
        let unscoped = S.plan(wanted: [], existing: [ahead], scope: nil, canRemove: true, calendarID: "cal-1", now: now)
        check(unscoped.deletes.isEmpty, "nothing is removed without a day to judge by")

        // An event with no block id is not Harbor's to judge.
        let stray = S.plan(wanted: [], existing: [event("e5", block: "")], scope: day, canRemove: true, calendarID: "cal-1", now: now)
        check(stray.isEmpty, "an event without a marker is never touched")

        // A block moved to another day updates its event even though that event is outside today.
        let tomorrow = block("task:later", start: now + 30 * hour, end: now + 31 * hour)
        let carry = S.plan(wanted: [tomorrow], existing: [event("e6", block: "task:later")], scope: nil, canRemove: true,
                           calendarID: "cal-1", now: now)
        check(carry.updates.map(\.eventID) == ["e6"], "a carried block moves its event")

        // Turning the sync off removes what is still ahead, never what already happened.
        let off = S.planRemovingAll(existing: [ahead, history, later, event("e7", block: "")], now: now)
        check(off.deletes == ["e1", "e2"], "off removes the upcoming ones Harbor added: \(off.deletes)")
    }

    // A block that drifts with the clock does not thrash its event.
    static func floatingChecks() {
        let soon = block("task:f", start: now + 5 * 60, end: now + 65 * 60, floating: true)
        check(S.plan(wanted: [soon], existing: [], scope: nil, canRemove: true, calendarID: "cal-1", now: now).creates.isEmpty,
              "a floating block starting within minutes waits")
        let later = block("task:f", start: now + 2 * hour, end: now + 3 * hour, floating: true)
        check(S.plan(wanted: [later], existing: [], scope: nil, canRemove: true, calendarID: "cal-1", now: now).creates == [later],
              "a floating block further out is written")
        let drifted = block("task:f", start: now + 2 * hour + 120, end: now + 3 * hour + 120, floating: true)
        let have = [event("e1", block: "task:f", start: now + 2 * hour, end: now + 3 * hour)]
        check(S.plan(wanted: [drifted], existing: have, scope: nil, canRemove: true, calendarID: "cal-1", now: now).isEmpty,
              "a two minute drift leaves the event alone")
        let shifted = block("task:f", start: now + 4 * hour, end: now + 5 * hour, floating: true)
        check(S.plan(wanted: [shifted], existing: have, scope: nil, canRemove: true, calendarID: "cal-1", now: now).updates.count == 1,
              "a real shift moves it")
        let exact = block("task:p", start: now + 2 * hour + 120, end: now + 3 * hour + 120)
        check(S.plan(wanted: [exact], existing: [event("e2", block: "task:p")], scope: nil, canRemove: true,
                     calendarID: "cal-1", now: now).updates.count == 1, "a pinned block moves for two minutes")
        // An event already under way is left alone.
        let underway = event("e3", block: "task:u", start: now - hour, end: now + hour)
        check(S.plan(wanted: [block("task:u", start: now + 3 * hour, end: now + 4 * hour)], existing: [underway],
                     scope: nil, canRemove: true, calendarID: "cal-1", now: now).updates.count == 1,
              "an event still ahead in part can move")
        let past = event("e4", block: "task:p2", start: now - 3 * hour, end: now - 2 * hour)
        check(S.plan(wanted: [block("task:p2", start: now + hour, end: now + 2 * hour)], existing: [past], scope: nil,
                     canRemove: true, calendarID: "cal-1", now: now).isEmpty, "an event that is over is history")
    }

    static func settingsAndPickerChecks() {
        let suite = UserDefaults(suiteName: "day-calendar-sync-checks-" + UUID().uuidString)!
        check(!DayCalendarSyncSettings.isEnabled(suite), "off until the person says yes")
        check(DayCalendarSyncSettings.calendarID(suite) == nil, "no calendar chosen yet")
        DayCalendarSyncSettings.set(enabled: true, calendarID: "cal-9", suite)
        check(DayCalendarSyncSettings.isEnabled(suite) && DayCalendarSyncSettings.calendarID(suite) == "cal-9", "yes is stored")
        DayCalendarSyncSettings.set(enabled: false, calendarID: nil, suite)
        check(!DayCalendarSyncSettings.isEnabled(suite) && DayCalendarSyncSettings.calendarID(suite) == "cal-9",
              "turning it off keeps the calendar chosen")

        let all = [
            DayCalendarChoice(id: "1", title: "Home", account: "iCloud", writable: true, isDefault: true),
            DayCalendarChoice(id: "2", title: "Work", account: "sam@example.com", writable: true, isDefault: false),
            DayCalendarChoice(id: "3", title: "Classes", account: "sam@example.com", writable: true, isDefault: false),
            DayCalendarChoice(id: "4", title: "US Holidays", account: "Subscribed", writable: false, isDefault: false),
            DayCalendarChoice(id: "5", title: "Birthdays", account: "iCloud", writable: false, isDefault: false),
        ]
        let groups = DayCalendarChoices.groups(all)
        check(groups.map(\.account) == ["iCloud", "sam@example.com"], "grouped by account, writable only: \(groups.map(\.account))")
        check(groups[1].choices.map(\.title) == ["Classes", "Work"], "calendars sorted by name")
        check(DayCalendarChoices.selection(all, stored: nil) == "1", "starts on the default calendar")
        check(DayCalendarChoices.selection(all, stored: "3") == "3", "keeps the chosen calendar")
        check(DayCalendarChoices.selection(all, stored: "4") == "1", "a read-only choice falls back to the default")
        check(DayCalendarChoices.selection(all, stored: "gone") == "1", "a deleted calendar falls back to the default")
        let noDefault = all.map { var c = $0; c.isDefault = false; return c }
        check(DayCalendarChoices.selection(noDefault, stored: nil) == "1", "no default: the first writable one")
        check(DayCalendarChoices.selection([all[3]], stored: nil) == nil, "nothing writable: nothing chosen")
    }

    // The setup card: its words, when it shows, and what Yes does.
    static func cardChecks() {
        typealias C = OnboardingCalendarSyncCard
        check(C.title == "Add what Harbor plans for you to your calendar?", "title")
        check(C.detail == "Study time, deadlines and to-dos Harbor finds in your mail and messages show up in your calendar, "
              + "in Google Calendar and the Calendar app. You can turn this off in Settings.", "detail")
        check(C.yesTitle == "Yes" && C.notNowTitle == "Not now", "buttons")
        for text in [C.title, C.detail, C.needsAccess, C.noCalendar] {
            check(!text.contains("Day"), "the person has not seen Day yet: \(text)")
        }
        check(!C.isShown(calendarAllowed: false) && C.isShown(calendarAllowed: true), "shown once Calendar is allowed")
        check(C.phase(enabled: false, declined: false) == .asking, "off and unanswered asks")
        check(C.phase(enabled: true, declined: false) == .on, "yes is on")
        check(C.phase(enabled: false, declined: true) == .off, "not now is off")

        let writable = [DayCalendarChoice(id: "1", title: "Home", account: "iCloud", writable: true, isDefault: true),
                        DayCalendarChoice(id: "2", title: "Work", account: "sam@example.com", writable: true, isDefault: false)]
        check(C.yes(hasFullAccess: true, choices: writable, selected: nil) == .enable(calendarID: "1"), "yes starts on the default")
        check(C.yes(hasFullAccess: true, choices: writable, selected: "2") == .enable(calendarID: "2"), "yes keeps the pick")
        check(C.yes(hasFullAccess: false, choices: writable, selected: nil) == .needsAccess, "no full access: refused")
        check(C.yes(hasFullAccess: true, choices: [], selected: nil) == .noCalendar, "no calendar to add to: refused")
        check(C.message(for: .needsAccess) == C.needsAccess && C.message(for: .noCalendar) == C.noCalendar
              && C.message(for: .enable(calendarID: "1")) == nil, "a refusal says why")
    }
}
