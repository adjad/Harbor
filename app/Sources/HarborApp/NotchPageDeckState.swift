import Foundation

/// Where a kept tab page stands in the open panel (NotchPageDeck).
enum NotchDeckRole: Equatable {
    /// The page on screen: hit-tested, read by VoiceOver, sized into the panel.
    case front
    /// The page that left at the last change: it fades out from where it
    /// was, drawn above the hidden ones, and stays so until the next change.
    /// Like a hidden page it is frozen and out of the panel's size.
    case leaving
    /// Kept, but invisible, disabled and out of the panel's size.
    case hidden
}

/// What a kept page was last laid out with while it was in front. A page out
/// of front keeps it, so a page fading out (or waiting hidden) is never laid
/// out again at the width or columns of the page that replaced it.
struct NotchDeckLayout: Equatable {
    var width: CGFloat
    var wide: Bool
}

/// Which tab pages the open notch keeps alive, and which one is in front.
///
/// A tab's page is built the first time it is shown, then kept while the
/// panel stays open: switching back finds it already built, at its scroll
/// position, instead of building it again (about 25-30 ms for Brief or Day).
/// Nothing is built ahead of being shown, so opening the panel costs what it
/// did. Closing the panel (or rebuilding it for a new text size or contrast)
/// lets them all go: the views go with the open panel.
///
/// Research is not kept: it shares Ask's field and focus, and it is rare.
/// Settings, permissions, the reset card and Getting to know you are not tabs
/// and keep their own transition.
///
/// Plain Foundation, so its checks compile without SwiftUI.
@MainActor
final class NotchDeckState {
    /// Kept tabs in the order they were first shown.
    private(set) var mounted: [NotchTab] = []
    private(set) var layouts: [NotchTab: NotchDeckLayout] = [:]
    private(set) var front: NotchTab?
    private(set) var leaving: NotchTab?
    private var page: NotchPage?
    private var generation: String?

    static func keepsAlive(_ tab: NotchTab) -> Bool { tab != .research }

    /// The kept tab a page shows, if it is one.
    static func keptTab(_ page: NotchPage) -> NotchTab? {
        if case .tab(let tab) = page, keepsAlive(tab) { return tab }
        return nil
    }

    /// Called with the page about to be drawn and what it is laid out with.
    /// `animated` is false with Reduce Motion: the old page goes at once, as
    /// its transition did. Returns true when this is a new page.
    /// `generation` names the panel's build (its text size and contrast): a
    /// new one rebuilt every view, so nothing kept from before is reused.
    @discardableResult
    func show(_ page: NotchPage, layout: NotchDeckLayout, strip: [NotchTab], animated: Bool,
              generation: String = "") -> Bool {
        if generation != self.generation {
            forget()
            self.generation = generation
        }
        let newFront = Self.keptTab(page)
        let changed = page != self.page
        if changed {
            leaving = animated && front != newFront ? front : nil
            self.page = page
        }
        front = newFront
        if let tab = newFront {
            if !mounted.contains(tab) { mounted.append(tab) }
            layouts[tab] = layout
        }
        // A wildcard tab turned off goes once it is out of sight.
        mounted.removeAll { $0 != front && $0 != leaving && !strip.contains($0) }
        layouts = layouts.filter { mounted.contains($0.key) }
        return changed
    }

    func role(_ tab: NotchTab) -> NotchDeckRole {
        if tab == front { return .front }
        if tab == leaving { return .leaving }
        return .hidden
    }

    /// Lets every kept page go but the one in front. Returns true when any
    /// went. The close calls it once the content is out of the picture, so
    /// the bar's turn after the roll-up takes down one page, not all of them.
    @discardableResult
    func releaseHidden() -> Bool {
        let keep = mounted.filter { $0 == front }
        guard keep.count != mounted.count else { return false }
        mounted = keep
        layouts = layouts.filter { mounted.contains($0.key) }
        leaving = nil
        return true
    }

    /// The panel closed or was rebuilt: nothing is kept.
    func forget() {
        mounted = []
        layouts = [:]
        front = nil
        leaving = nil
        page = nil
    }
}

/// When a page's own "shown" and "hidden" work runs (onNotchPageShown,
/// onNotchPageHidden): every time the page comes to the front and every time
/// it leaves it, and exactly as onAppear/onDisappear outside the deck.
enum NotchPageLifecycle {
    enum Event: Equatable { case shown, hidden }

    /// The view appeared: it counts as shown only if its page is in front (a
    /// view added inside a hidden page waits for the page to come forward).
    static func appeared(pageShown: Bool) -> Event? { pageShown ? .shown : nil }
    /// The view went away: hidden, unless its page had already left the front.
    static func disappeared(pageShown: Bool) -> Event? { pageShown ? .hidden : nil }
    /// Its page came forward or left the front while the view stayed.
    static func pageChanged(to shown: Bool) -> Event { shown ? .shown : .hidden }
}
