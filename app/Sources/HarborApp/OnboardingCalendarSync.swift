import Foundation

// The setup card that asks once whether what Harbor plans for the person goes on
// their calendar. It sits under the Calendar row of the permissions step and is
// drawn only after Calendar is allowed. Off until the person says Yes. It never
// mentions the Day tab: the person has not seen it yet. The rules for what is written
// and where are DayCalendarSync.swift; Foundation only, so a check can compile it alone.
//
// Rules:
// - IMPORTANT: nothing is written, and the choice stays off, until the person taps Yes.
// - IMPORTANT: Yes needs full Calendar access and a writable calendar. When either is
//   missing the card says so and stays off, never claiming it is on.
// - IMPORTANT: the picker lists writable calendars only, grouped by account, and starts
//   on the person's default calendar.

enum OnboardingCalendarSyncCard {
    static let title = "Add what Harbor plans for you to your calendar?"
    static let detail = "Study time, deadlines and to-dos Harbor finds in your mail and messages show up in your "
        + "calendar, in Google Calendar and the Calendar app. You can turn this off in Settings."
    static let yesTitle = "Yes"
    static let notNowTitle = "Not now"
    static let pickerLabel = "Calendar"
    static let turnOnTitle = "Turn on"
    static let onLabel = "On"
    static let offLabel = "Off"
    static let needsAccess = "Harbor needs full Calendar access for this. Choose Full Access for Harbor in "
        + "System Settings > Privacy & Security > Calendars."
    static let noCalendar = "None of your calendars can be added to. Add one in the Calendar app first."
    static let icon = "calendar.badge.plus"

    /// What the card shows on its right.
    enum Phase: Equatable { case asking, on, off }

    /// The card is drawn once Calendar is allowed, and not before.
    static func isShown(calendarAllowed: Bool) -> Bool { calendarAllowed }

    /// `enabled` is the stored choice; `declined` is "Not now" tapped here.
    static func phase(enabled: Bool, declined: Bool) -> Phase {
        enabled ? .on : (declined ? .off : .asking)
    }

    enum Decision: Equatable {
        case enable(calendarID: String)
        case needsAccess
        case noCalendar
    }

    /// What Yes does, from macOS's answer and the calendars this Mac has.
    static func yes(hasFullAccess: Bool, choices: [DayCalendarChoice], selected: String?) -> Decision {
        guard hasFullAccess else { return .needsAccess }
        guard let id = DayCalendarChoices.selection(choices, stored: selected) else { return .noCalendar }
        return .enable(calendarID: id)
    }

    /// The line under the card for a refused Yes, or nil.
    static func message(for decision: Decision) -> String? {
        switch decision {
        case .enable: return nil
        case .needsAccess: return needsAccess
        case .noCalendar: return noCalendar
        }
    }
}
