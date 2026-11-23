import SwiftUI

// The tour's pictures of Brief, Day and Ask (OnboardingTour.swift): sample data
// only, in a dark notch-shaped frame, drawn with the notch's own tokens and
// pieces (Theme, .harbor fonts, DayInk bars, BriefReady tags, ProactiveCard and
// MarkdownView). The notch's own views (NotchBriefView, NotchDayView) read the
// service, the calendar and shared models as they appear, so they are not used
// here: nothing below reads real data, reaches the service, or can be clicked.

enum OnboardingMockups {
    /// The Brief's two cards, as the service would send them (invented).
    static let freeTimeCard = ProactiveCard(
        id: "example-free", meta: "TODAY · CALENDAR", title: OnboardingTour.briefHeroTitle,
        body: OnboardingTour.briefHeroBody, why: "",
        action: ProactiveCard.Action(method: "block_time", button: OnboardingTour.briefHeroButton))
    static let mailCard = ProactiveCard(
        id: "example-mail", meta: "NEW · MAIL", title: OnboardingTour.briefTitle,
        body: OnboardingTour.briefBody, why: "",
        action: ProactiveCard.Action(method: "email_reply", button: OnboardingTour.briefButton,
                                     draft: "Yes, 7 works. See you there."))

    struct DayRow: Equatable {
        let top: String
        let bottom: String
        let title: String
        let detail: String
        let style: DayStyle
    }

    /// The agenda's rows, in the notch's gutter wording (DayTimeline.gutter).
    static let dayRows: [DayRow] = [
        DayRow(top: "9:30 AM", bottom: "10:00 AM", title: "Team stand-up", detail: "Calendar · 30 min", style: .calendar),
        DayRow(top: "12:00 PM", bottom: "1:00 PM", title: "Lunch with Priya", detail: "Calendar · 1 h", style: .calendar),
        DayRow(top: "5:00 PM", bottom: "Due", title: "Send the budget draft", detail: "To-do", style: .todo),
        DayRow(top: "Anytime", bottom: "", title: "Call the dentist", detail: "To-do", style: .todo),
    ]
}

/// The notch's dark panel, drawn small: black, rounded at the bottom, with the
/// "Example" mark inside it. Never takes a click.
struct OnboardingNotchFrame<Content: View>: View {
    let label: String
    @ViewBuilder var content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: 8, content: content)
            .padding(.horizontal, 14).padding(.top, 14).padding(.bottom, 14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(UnevenRoundedRectangle(bottomLeadingRadius: 18, bottomTrailingRadius: 18).fill(Color.black))
            .overlay(UnevenRoundedRectangle(bottomLeadingRadius: 18, bottomTrailingRadius: 18)
                .strokeBorder(Theme.hairline, lineWidth: 1))
            .overlay(alignment: .topTrailing) {
                Text(OnboardingTour.cardMark).font(.harbor(size: 9.5, weight: .semibold)).tracking(0.6)
                    .foregroundStyle(Theme.textMuted)
                    .padding(.horizontal, 6).padding(.vertical, 2)
                    .background(Capsule().fill(Theme.chipFill))
                    .padding(8)
            }
            .allowsHitTesting(false)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(label)
    }
}

/// The Brief: a large free-time card with its one button, then a compact mail row.
struct OnboardingBriefMock: View {
    private let hero = OnboardingMockups.freeTimeCard
    private let mail = OnboardingMockups.mailCard

    var body: some View {
        OnboardingNotchFrame(label: "Example Brief") {
            heroCard
            mailRow
        }
    }

    private func tag(_ text: String) -> some View {
        Text(text).font(.harbor(size: 9, weight: .semibold, design: .monospaced)).tracking(0.3)
            .foregroundStyle(Theme.textSecondary).lineLimit(1).fixedSize()
            .padding(.horizontal, 5).padding(.vertical, 2)
            .overlay(RoundedRectangle(cornerRadius: 4).stroke(Theme.chipStroke, lineWidth: 1))
    }

    private var heroCard: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                tag(BriefReady.cardTag(method: hero.action?.method ?? "", hasDraft: false))
                Text(hero.metaLine).font(.harbor(size: 10, weight: .medium, design: .monospaced)).tracking(0.6)
                    .foregroundStyle(Theme.textMuted).lineLimit(1)
                Spacer(minLength: 4)
                Image(systemName: "xmark").font(.harbor(size: 10.5, weight: .semibold)).foregroundStyle(Theme.textMuted)
            }
            Text(hero.title).font(.harbor(size: 15.5, weight: .semibold)).foregroundStyle(Theme.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            Text(hero.body).font(.harbor(.body)).foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 12) {
                Text(hero.action?.label ?? "").font(.harbor(size: 12.5, weight: .semibold)).foregroundStyle(Color.black)
                    .lineLimit(1)
                    .padding(.horizontal, 13).padding(.vertical, 5)
                    .background(Capsule().fill(Color.white.opacity(0.92)))
                Text("Pick another time").font(.harbor(size: 12, weight: .medium)).foregroundStyle(Theme.textSecondary)
                Spacer(minLength: 4)
            }
            .padding(.top, 3)
        }
        .padding(EdgeInsets(top: 9, leading: 13, bottom: 10, trailing: 9))
        .frame(maxWidth: .infinity, alignment: .leading)
        // ProactiveCards.swift keeps its cardFill and cardStroke private: same values.
        .background(RoundedRectangle(cornerRadius: 12).fill(Color.white.opacity(0.055)))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.white.opacity(0.13), lineWidth: 1))
    }

    private var mailRow: some View {
        HStack(alignment: .center, spacing: 8) {
            tag(BriefReady.cardTag(method: mail.action?.method ?? "", hasDraft: true))
            VStack(alignment: .leading, spacing: 1) {
                Text(mail.chipTitle).font(.harbor(size: 13, weight: .semibold)).foregroundStyle(Theme.textPrimary).lineLimit(1)
                Text(mail.body).font(.harbor(size: 11.5)).foregroundStyle(Theme.textMuted).lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Text(mail.action?.label ?? "").font(.harbor(size: 10.5, weight: .medium)).foregroundStyle(Theme.textSecondary)
                .lineLimit(1)
                .padding(.horizontal, 8).padding(.vertical, 3)
                .background(Capsule().fill(Color.black.opacity(0.35)))
                .overlay(Capsule().stroke(Theme.chipStroke, lineWidth: 1))
                .fixedSize()
        }
        .padding(.vertical, 5)
        .overlay(alignment: .top) { Rectangle().fill(Theme.hairline).frame(height: 1) }
    }
}

/// The Day's agenda rows: time column, ink bar, title and one muted line.
struct OnboardingDayMock: View {
    var body: some View {
        OnboardingNotchFrame(label: "Example Day") {
            ForEach(OnboardingMockups.dayRows, id: \.title) { row in
                HStack(alignment: .top, spacing: 10) {
                    VStack(alignment: .leading, spacing: 1) {
                        Text(row.top).font(.harbor(.body, weight: .semibold)).foregroundStyle(Theme.textPrimary)
                        if !row.bottom.isEmpty {
                            Text(row.bottom).font(.harbor(.footnote)).foregroundStyle(Theme.textMuted)
                        }
                    }
                    .monospacedDigit()
                    .padding(.top, 1)
                    .frame(width: 62 * Theme.textScale, alignment: .leading)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(row.title).font(.harbor(.callout, weight: .semibold)).foregroundStyle(Theme.textPrimary)
                        Text(row.detail).font(.harbor(.footnote)).foregroundStyle(DayInk.detail(row.style))
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.leading, 12)
                    .overlay(alignment: .leading) { DayInk.bar(row.style).frame(width: 3) }
                }
                .padding(.vertical, 5)
            }
        }
    }
}

/// Ask: the person's question as a chip, and Harbor's answer as the notch draws it
/// (MarkdownView), a plain sentence.
struct OnboardingAskMock: View {
    var body: some View {
        OnboardingNotchFrame(label: "Example Ask") {
            HStack {
                Spacer(minLength: 40)
                Text(OnboardingTour.askQuestion)
                    .font(.harbor(.reading)).foregroundStyle(Theme.textPrimary)
                    .padding(.horizontal, 12).padding(.vertical, 7)
                    .background(RoundedRectangle(cornerRadius: 12).fill(Theme.chipFill))
                    .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.chipStroke, lineWidth: 1))
            }
            MarkdownView(text: OnboardingTour.askAnswer)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}
