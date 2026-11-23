import Foundation

// The last onboarding page, "Get more from Harbor": what it offers and in what
// order. Setup itself asks only for Calendar, Reminders and Contacts
// (FirstRunPermissions.Item.setup); this page adds the access that needs System
// Settings. Everything on it is optional, and finishing it with nothing granted
// finishes setup exactly as before. Screen Recording and the microphone are not
// asked here: they are asked when first used. Foundation only, so a check can
// compile it alone.

enum OnboardingAccessPage {
    static let title = "Get more from Harbor"
    static let subtitle = "All optional. Harbor works without any of these, and each can be switched on later in Settings."

    /// A row on the page. Full Disk Access is the main one; the others sit below it.
    enum Row: String, CaseIterable, Equatable {
        case fullDiskAccess, messagesAutomation, mailNotesAutomation

        var title: String {
            switch self {
            case .fullDiskAccess: return "Full Disk Access"
            case .messagesAutomation: return "Messages automation"
            case .mailNotesAutomation: return "Mail and Notes automation"
            }
        }

        var detail: String {
            switch self {
            case .fullDiskAccess:
                return "Lets Harbor read Mail, Messages and Notes even when those apps are closed."
            case .messagesAutomation:
                return "So Harbor can send a text you've approved from Ask."
            case .mailNotesAutomation:
                return "So Harbor can read Mail and Notes while they are open, if you'd rather not use Full Disk Access."
            }
        }
    }

    /// Top to bottom.
    static let order: [Row] = [.fullDiskAccess, .messagesAutomation, .mailNotesAutomation]

    /// Only Full Disk Access is drawn large; the rest are the quiet optional list.
    static func isProminent(_ row: Row) -> Bool { row == .fullDiskAccess }

    /// The button on Full Disk Access: it opens System Settings, since macOS can't ask.
    static let openSettingsTitle = "Open System Settings"
    static let doneTitle = "Done \u{2014} open Harbor"
    static let grantedLabel = "Allowed"
    static let notGrantedLabel = "Not allowed yet"
    static let optionalHeading = "Also optional"
    /// Said under the optional rows: nothing here is needed to finish.
    static let footnote = "Screen Recording and the microphone are asked for the first time you use them."

    /// What the page's footer shows: the page can always be finished.
    static let canFinishWithNothingGranted = true
}
