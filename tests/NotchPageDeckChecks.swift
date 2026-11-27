import Foundation

/// The notch's kept tab pages (NotchDeckState) and when a kept page's own
/// "shown" and "hidden" work runs (NotchPageLifecycle). Plain Foundation:
/// compiled with NotchTab, WildcardTabs and NotchPageDeckState
/// only, no SwiftUI and no window. The real panel's behaviour (Brief's and
/// Day's hooks on every switch, timing, memory) is in NotchMotionChecks.
@main
struct NotchPageDeckChecks {
    @MainActor static func main() {
        var count = 0, failed = 0
        func check(_ c: Bool, _ m: String) { count += 1; if !c { failed += 1; print("FAIL: \(m)") } }

        let strip: [NotchTab] = [.ask, .brief, .day, .meet, .study]
        let narrow = NotchDeckLayout(width: 436, wide: false)
        let wide = NotchDeckLayout(width: 796, wide: true)

        // Built on first show only: opening on Ask keeps Ask and nothing else.
        let deck = NotchDeckState()
        check(deck.show(.tab(.ask), layout: narrow, strip: strip, animated: true), "the first page is a change")
        check(deck.mounted == [.ask], "opening keeps only the tab shown: \(deck.mounted)")
        check(deck.role(.ask) == .front && deck.leaving == nil, "Ask in front, nothing leaving")
        check(!deck.show(.tab(.ask), layout: narrow, strip: strip, animated: true), "the same page again is no change")

        // A switch keeps the page that left, leaving until the next change.
        deck.show(.tab(.brief), layout: wide, strip: strip, animated: true)
        check(deck.mounted == [.ask, .brief], "Brief is added when first shown: \(deck.mounted)")
        check(deck.role(.brief) == .front && deck.role(.ask) == .leaving, "Brief in front, Ask leaving")
        deck.show(.tab(.day), layout: narrow, strip: strip, animated: true)
        check(deck.leaving == .brief && deck.role(.ask) == .hidden,
              "the next change: Brief leaving, Ask hidden, with no redraw of its own between")
        deck.show(.tab(.day), layout: narrow, strip: strip, animated: true)
        check(deck.leaving == .brief, "drawing the same page again changes nothing")
        check(deck.mounted == [.ask, .brief, .day], "three tabs shown, three kept: \(deck.mounted)")

        // A page out of front keeps the layout it was last in front with.
        check(deck.layouts[.brief] == wide && deck.layouts[.day] == narrow,
              "Brief keeps its wide layout while Day is in front")
        deck.show(.tab(.brief), layout: narrow, strip: strip, animated: true)
        check(deck.layouts[.brief] == narrow, "back in front, Brief takes the panel's layout")
        check(deck.mounted == [.ask, .brief, .day], "coming back builds nothing new")

        // Reduce Motion: the old page goes at once, as its transition did.
        deck.show(.tab(.ask), layout: narrow, strip: strip, animated: false)
        check(deck.leaving == nil && deck.role(.brief) == .hidden, "with Reduce Motion nothing is left fading")

        // Pages that are not kept tabs: Research, quick settings, permissions.
        deck.show(.tab(.research), layout: narrow, strip: strip, animated: true)
        check(!deck.mounted.contains(.research) && deck.front == nil, "Research is never kept")
        check(deck.leaving == .ask, "Ask fades out under Research")
        deck.show(.settings, layout: narrow, strip: strip, animated: true)
        check(deck.front == nil && deck.mounted.count == 3, "quick settings over the kept tabs keeps them")
        check(NotchDeckState.keptTab(.settings) == nil && NotchDeckState.keptTab(.permissions) == nil
              && NotchDeckState.keptTab(.reset) == nil && NotchDeckState.keptTab(.knowYou) == nil
              && NotchDeckState.keptTab(.tab(.study)) == .study, "only tab pages are kept")

        // A wildcard tab turned off goes once it is out of sight.
        deck.show(.tab(.meet), layout: narrow, strip: strip, animated: false)
        check(deck.mounted.contains(.meet), "Meet kept")
        let noMeet: [NotchTab] = [.ask, .brief, .day, .study]
        deck.show(.tab(.meet), layout: narrow, strip: noMeet, animated: false)
        check(deck.mounted.contains(.meet), "Meet stays while it is still in front")
        deck.show(.tab(.ask), layout: narrow, strip: noMeet, animated: false)
        check(!deck.mounted.contains(.meet) && deck.layouts[.meet] == nil, "Meet goes once it has left the strip and the front")

        // The close lets the hidden pages go and keeps the one in front.
        let closing = NotchDeckState()
        for tab in [NotchTab.ask, .brief, .day, .study] {
            closing.show(.tab(tab), layout: narrow, strip: strip, animated: true)
        }
        check(closing.releaseHidden() && closing.mounted == [.study] && closing.leaving == nil
              && closing.layouts.keys.sorted { $0.rawValue < $1.rawValue } == [.study],
              "a close keeps only the page in front: \(closing.mounted)")
        check(!closing.releaseHidden(), "a second release has nothing to let go")
        closing.show(.tab(.ask), layout: narrow, strip: strip, animated: true)
        check(closing.mounted == [.study, .ask], "a close cut short builds the next page again: \(closing.mounted)")

        // Closing the panel, or a rebuild for text size or contrast: nothing kept.
        deck.forget()
        check(deck.mounted.isEmpty && deck.front == nil && deck.leaving == nil, "closing keeps nothing")
        let rebuilt = NotchDeckState()
        rebuilt.show(.tab(.ask), layout: narrow, strip: strip, animated: true, generation: "13-0")
        rebuilt.show(.tab(.day), layout: narrow, strip: strip, animated: true, generation: "13-0")
        rebuilt.show(.tab(.day), layout: narrow, strip: strip, animated: true, generation: "15-0")
        check(rebuilt.mounted == [.day] && rebuilt.leaving == nil, "a new text size keeps only the page in front: \(rebuilt.mounted)")

        // When a kept page's own work runs.
        typealias L = NotchPageLifecycle
        check(L.appeared(pageShown: true) == .shown, "appearing in front runs the shown work")
        check(L.appeared(pageShown: false) == nil, "appearing inside a hidden page waits for it to come forward")
        check(L.disappeared(pageShown: true) == .hidden, "going while in front runs the hidden work")
        check(L.disappeared(pageShown: false) == nil, "going after the page left the front runs nothing twice")
        check(L.pageChanged(to: true) == .shown && L.pageChanged(to: false) == .hidden, "coming forward and leaving")
        // A run of switches: appear, then leave and come back three times, then close.
        var events: [L.Event] = []
        events += [L.appeared(pageShown: true)].compactMap { $0 }
        for _ in 0..<3 { events += [L.pageChanged(to: false), L.pageChanged(to: true)] }
        events += [L.disappeared(pageShown: true)].compactMap { $0 }
        check(events == [.shown, .hidden, .shown, .hidden, .shown, .hidden, .shown, .hidden],
              "shown and hidden alternate, once per switch: \(events)")

        print("\(count - failed)/\(count) notch page deck checks passed")
        exit(failed == 0 ? 0 : 1)
    }
}
