import SwiftUI

// A short look at Brief, Day and Ask while the model downloads (How to use).
// Sample cards only, each marked "Example": nothing here reads the person's
// data, asks the service or the model, or can be clicked into doing anything.

enum OnboardingTour {
    struct Page: Equatable {
        let tab: String
        let line: String
    }

    /// One page per notch tab, in the notch's order of a morning.
    static let pages: [Page] = [
        Page(tab: "Brief", line: "Each morning Harbor gathers what needs you, and gets a reply ready for you to check."),
        Page(tab: "Day", line: "Your meetings and to-dos in time order, so you can see the shape of your day."),
        Page(tab: "Ask", line: "Ask in your own words. Harbor looks through your mail, calendar and notes to answer."),
    ]

    /// Where Next and Back lead, staying on the pages. Pure.
    static func step(_ page: Int, by delta: Int) -> Int {
        min(max(page + delta, 0), pages.count - 1)
    }

    /// "2 of 3", for the dots' accessibility value. Pure.
    static func position(_ page: Int) -> String { "\(page + 1) of \(pages.count)" }

    /// On every sample card, not only the header.
    static let cardMark = "EXAMPLE"

    // The examples, kept here so the copy can be checked. Invented names and times.
    static let briefHeroTitle = "Study for STAT 101 midterm"
    static let briefHeroBody = "You're free 2\u{2013}3 PM after Lab. I can hold that hour."
    static let briefHeroButton = "Block 2\u{2013}3 PM?"
    static let briefMeta = "NEW · MAIL"
    static let briefTitle = "Reply to Sam about Friday's dinner"
    static let briefBody = "Sam asked if 7 still works. I wrote a short yes for you to read first."
    static let briefButton = "Review reply"
    static let dayRows: [(time: String, title: String)] = [
        ("9:30", "Team stand-up"),
        ("12:00", "Lunch with Priya"),
        ("Due 5 PM", "Send the budget draft"),
        ("Anytime", "Call the dentist"),
    ]
    static let askQuestion = "What's due this week?"
    static let askAnswer = "Your budget draft is due Thursday at 5 PM, and the STAT 101 problem set is due Friday morning."
}

struct OnboardingTourView: View {
    @State private var page = 0
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let current = OnboardingTour.pages[page]
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Text("A quick look").font(.system(size: 13, weight: .medium))
                OnboardingPill(text: "Example")
                Spacer()
                dots
            }
            Text(current.tab).font(.system(size: 12, weight: .semibold)).foregroundStyle(OnboardingPalette.lilac)
                .accessibilityAddTraits(.isHeader)
            Text(current.line).font(.system(size: 12)).foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            Group {
                switch page {
                case 0: OnboardingBriefMock()
                case 1: OnboardingDayMock()
                default: OnboardingAskMock()
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .transition(reduceMotion ? .opacity : .opacity.combined(with: .move(edge: .trailing)))
            .id(page)
            .accessibilityElement(children: .combine)
            .accessibilityLabel("Example \(current.tab)")
            HStack {
                Button("Back") { turn(-1) }
                    .buttonStyle(FirstRunButtonStyle())
                    .disabled(page == 0)
                    .accessibilityHint("Example pages only; nothing is changed")
                Spacer()
                // On the last page: back to the first example page. The hint below says setup is untouched.
                Button(page == OnboardingTour.pages.count - 1 ? "Start over" : "Next") {
                    turn(page == OnboardingTour.pages.count - 1 ? -page : 1)
                }
                .buttonStyle(FirstRunButtonStyle())
                .accessibilityHint("Example pages only; nothing is changed")
            }
        }
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 12).fill(Color.white.opacity(0.03)))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.hairline))
        .padding(.bottom, 14)
    }

    private func turn(_ delta: Int) {
        withAnimation(reduceMotion ? nil : .easeOut(duration: 0.2)) { page = OnboardingTour.step(page, by: delta) }
    }

    private var dots: some View {
        HStack(spacing: 5) {
            ForEach(OnboardingTour.pages.indices, id: \.self) { i in
                Circle().fill(i == page ? Theme.textPrimary : Theme.textMuted.opacity(0.5)).frame(width: 6, height: 6)
            }
        }
        .accessibilityElement()
        .accessibilityLabel("Page")
        .accessibilityValue(OnboardingTour.position(page))
    }
}
