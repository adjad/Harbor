import AppKit
import SwiftUI

/// The calendar picker shared by Settings and the setup card: writable calendars,
/// grouped by account, one chosen.
struct DayCalendarPickerMenu: View {
    let choices: [DayCalendarChoice]
    @Binding var selection: String

    var body: some View {
        Picker(OnboardingCalendarSyncCard.pickerLabel, selection: $selection) {
            ForEach(DayCalendarChoices.groups(choices)) { group in
                Section(group.account) {
                    ForEach(group.choices) { choice in Text(choice.title).tag(choice.id) }
                }
            }
        }
        .labelsHidden()
        .fixedSize()
    }
}

/// Settings: whether what Harbor plans goes on the calendar, and which calendar.
/// Turning it on asks for full Calendar access if Harbor doesn't have it; turning it
/// off removes the upcoming events Harbor added, and touches nothing else.
struct DayCalendarSettingsRow: View {
    @State private var enabled = DayCalendarSyncSettings.isEnabled()
    @State private var choices: [DayCalendarChoice] = []
    @State private var selection = ""
    @State private var message: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Toggle(isOn: Binding(get: { enabled }, set: { turn($0) })) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Add Harbor's plan to your calendar").font(.system(size: 14, weight: .medium))
                    Text(enabled
                         ? "Study time, deadlines and to-dos Harbor plans for you are added to the calendar below, "
                           + "and reach Google Calendar if that is one of your accounts. Turning this off removes "
                           + "the upcoming ones Harbor added. Events you made are never touched."
                         : "Off. Harbor's plan stays in Harbor.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            .toggleStyle(.switch)
            if enabled && !choices.isEmpty {
                HStack(spacing: 8) {
                    Text("Calendar").font(.caption).foregroundStyle(.secondary)
                    DayCalendarPickerMenu(choices: choices, selection: Binding(
                        get: { selection }, set: { chose($0) }))
                }
            }
            if let message {
                Text(message).font(.caption).foregroundStyle(.secondary)
            }
        }
        .task { load() }
    }

    private func load() {
        choices = DayCalendarWriter.choices()
        selection = DayCalendarChoices.selection(choices, stored: DayCalendarSyncSettings.calendarID()) ?? ""
        if enabled, let problem = DayCalendarWriter.shared.problem { message = problem }
    }

    private func chose(_ id: String) {
        selection = id
        DayCalendarSyncSettings.set(enabled: true, calendarID: id)
    }

    private func turn(_ on: Bool) {
        guard on else {
            enabled = false
            message = nil
            DayCalendarSyncSettings.set(enabled: false, calendarID: nil)
            return
        }
        Task { @MainActor in
            let access = await DayCalendarWriter.ensureFullAccess()
            choices = DayCalendarWriter.choices()
            let decision = OnboardingCalendarSyncCard.yes(hasFullAccess: access, choices: choices,
                                                          selected: DayCalendarSyncSettings.calendarID())
            message = OnboardingCalendarSyncCard.message(for: decision)
            guard case .enable(let id) = decision else {
                enabled = false
                if !access { Permissions.openSettingsPane("com.apple.preference.security?Privacy_Calendars") }
                return
            }
            selection = id
            enabled = true
            DayCalendarSyncSettings.set(enabled: true, calendarID: id)
        }
    }
}
