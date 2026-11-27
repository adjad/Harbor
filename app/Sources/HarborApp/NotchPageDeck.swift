import Combine
import SwiftUI

// MARK: - Shown or hidden

private struct NotchPageShownKey: EnvironmentKey {
    static let defaultValue = true
}

extension EnvironmentValues {
    /// Whether the notch page this view is on is the one in front. True
    /// everywhere outside the notch's kept pages (NotchPageDeck), so a view
    /// reading it behaves as before in the Harbor window or a sheet.
    var notchPageShown: Bool {
        get { self[NotchPageShownKey.self] }
        set { self[NotchPageShownKey.self] = newValue }
    }
}

extension View {
    /// Runs `action` each time this view's notch page comes to the front, and
    /// when the view appears on a page already in front: onAppear for a page
    /// that is kept while hidden (NotchPageDeck). Outside the deck, onAppear.
    func onNotchPageShown(perform action: @escaping () -> Void) -> some View {
        modifier(NotchPageHook(onShown: action, onHidden: nil))
    }

    /// Runs `action` each time this view's notch page leaves the front, and
    /// when the view goes while its page is in front: onDisappear for a kept
    /// page. Outside the deck, onDisappear.
    func onNotchPageHidden(perform action: @escaping () -> Void) -> some View {
        modifier(NotchPageHook(onShown: nil, onHidden: action))
    }

    /// `.task` that runs each time the page comes to the front and is
    /// cancelled when it leaves: a hidden page reads and polls nothing.
    func notchPageTask(_ action: @escaping () async -> Void) -> some View {
        modifier(NotchPageTask(id: 0, action: action))
    }

    /// `.task(id:)` that also stops while the page is hidden and starts again
    /// when it comes back.
    func notchPageTask<ID: Equatable>(id: ID, _ action: @escaping () async -> Void) -> some View {
        modifier(NotchPageTask(id: id, action: action))
    }
}

private struct NotchPageHook: ViewModifier {
    let onShown: (() -> Void)?
    let onHidden: (() -> Void)?
    @Environment(\.notchPageShown) private var shown

    func body(content: Content) -> some View {
        content
            .onAppear { fire(NotchPageLifecycle.appeared(pageShown: shown)) }
            .onDisappear { fire(NotchPageLifecycle.disappeared(pageShown: shown)) }
            .onChange(of: shown) { _, now in fire(NotchPageLifecycle.pageChanged(to: now)) }
    }

    private func fire(_ event: NotchPageLifecycle.Event?) {
        switch event {
        case .shown: onShown?()
        case .hidden: onHidden?()
        case nil: break
        }
    }
}

private struct NotchPageTaskKey<ID: Equatable>: Equatable {
    let id: ID
    let shown: Bool
}

private struct NotchPageTask<ID: Equatable>: ViewModifier {
    let id: ID
    let action: () async -> Void
    @Environment(\.notchPageShown) private var shown

    func body(content: Content) -> some View {
        content.task(id: NotchPageTaskKey(id: id, shown: shown)) {
            guard shown else { return }
            await action()
        }
    }
}

extension View {
    /// `.onChange(of:)` that acts only while this view's notch page is the
    /// one shown: a kept, hidden page never answers a change made by the page
    /// in front (a hidden Brief pulling Day back to today).
    func onNotchPageChange<V: Equatable>(of value: V, perform action: @escaping () -> Void) -> some View {
        modifier(NotchPageChange(value: value, action: action))
    }
}

private struct NotchPageChange<V: Equatable>: ViewModifier {
    let value: V
    let action: () -> Void
    @Environment(\.notchPageShown) private var shown

    func body(content: Content) -> some View {
        content.onChange(of: value) { _, _ in if shown { action() } }
    }
}

/// `@ObservedObject` for a notch tab's page that stops following the object
/// while the page is kept hidden (NotchPageDeck): a streamed token or a key
/// in Ask then redraws nothing in a hidden Brief, Day or Study. Coming back
/// to the front redraws it from the object as it is now, and follows it
/// again. Outside the deck, the page is always shown: exactly @ObservedObject.
@propertyWrapper
struct NotchPageObserved<Object: ObservableObject>: DynamicProperty {
    @StateObject private var relay = NotchPageRelay()
    @Environment(\.notchPageShown) private var shown
    // Settable so a view's own init can assign it (`self.model = model`).
    var wrappedValue: Object

    init(wrappedValue: Object) { self.wrappedValue = wrappedValue }

    var projectedValue: ObservedObject<Object>.Wrapper { ObservedObject(wrappedValue: wrappedValue).projectedValue }

    func update() { relay.follow(wrappedValue, shown: shown) }
}

/// Passes one object's changes on to the view holding it, while asked to.
@MainActor
final class NotchPageRelay: ObservableObject {
    private var following: ObjectIdentifier?
    private var subscription: AnyCancellable?

    func follow<Object: ObservableObject>(_ object: Object, shown: Bool) {
        let id = shown ? ObjectIdentifier(object) : nil
        guard id != following else { return }
        following = id
        subscription = shown ? object.objectWillChange.sink { [weak self] _ in self?.objectWillChange.send() } : nil
    }
}

/// `moving` while its notch page is in front, `still` while the page is kept
/// hidden. A repeating animation is stopped by taking it out of the tree: a
/// nil animation on a live repeatForever does not reliably stop it (HarborOrb).
struct StillWhenHidden<Moving: View, Still: View>: View {
    @ViewBuilder let moving: () -> Moving
    @ViewBuilder let still: () -> Still
    @Environment(\.notchPageShown) private var shown

    var body: some View {
        if shown { moving() } else { still() }
    }
}

// MARK: - The kept pages

private struct NotchDeckRoleKey: LayoutValueKey {
    static let defaultValue = NotchDeckRole.hidden
}

/// Kept pages stacked at the top leading corner, as the panel's ZStack laid
/// out its one page: only the page in front counts toward the size, so the
/// surface glides to its height while a page fading out is clipped. A page
/// out of front is placed at its own height and its last width, so it is
/// not laid out again until it comes back, and it never makes the panel taller.
private struct NotchDeckStack: Layout {
    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        var size = CGSize.zero
        // The page in front only: a page fading out is clipped by the surface
        // as the surface glides to the new height, as on its transition.
        for subview in subviews where subview[NotchDeckRoleKey.self] == .front {
            let fit = subview.sizeThatFits(proposal)
            size.width = max(size.width, fit.width)
            size.height = max(size.height, fit.height)
        }
        return CGSize(width: proposal.width ?? size.width, height: size.height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        for subview in subviews {
            let hidden = subview[NotchDeckRoleKey.self] == .hidden
            subview.place(at: bounds.origin, anchor: .topLeading,
                          proposal: hidden ? ProposedViewSize(width: proposal.width, height: nil) : proposal)
        }
    }
}

/// A kept page out of the front: shifted the step it would arrive from (or
/// the step it is leaving to) and faded. In front: where it belongs.
private struct NotchDeckShift: ViewModifier {
    let steps: Double
    let visible: Bool

    func body(content: Content) -> some View {
        content
            .offset(x: CGFloat(steps) * Theme.Motion.slideStep)
            .opacity(visible ? 1 : 0)
    }
}

/// A kept page's content, drawn again from the panel only while it is in
/// front. A page fading out keeps its last frame, as a page leaving on its
/// transition did, and a hidden one keeps what it last drew (its own views
/// still follow their models), so a change elsewhere in the panel (a
/// streamed token, a key) costs nothing in the pages that cannot be seen.
private struct NotchDeckPage<Content: View>: View, Equatable {
    let tab: NotchTab
    let role: NotchDeckRole
    let layout: NotchDeckLayout
    @ViewBuilder let content: () -> Content

    static func == (a: Self, b: Self) -> Bool {
        a.role != .front && b.role != .front && a.tab == b.tab && a.layout == b.layout
    }

    var body: some View {
        PinnedWidth(width: layout.width) {
            VStack(alignment: .leading, spacing: 0) { content() }
        }
    }
}

/// The open panel's tab pages, each built the first time it is shown and
/// kept while the panel is open (NotchDeckState). The page in front is drawn
/// on top, takes the clicks, keyboard and VoiceOver; the others are hidden,
/// disabled and out of the panel's size. A change of page moves them as the
/// page transition did: the arriving page slides in by its step and fades in,
/// the leaving one slides by its step and fades out, on the panel's spring.
struct NotchPageDeck<Page: View>: View {
    let deck: NotchDeckState
    let page: NotchPage
    let slide: NotchPageSlide
    /// The open's and close's veil (OverlayModel.contentVeiled): it fades the
    /// page in front only, so a close never redraws the hidden ones.
    var veiled = false
    let content: (NotchTab, NotchDeckLayout) -> Page

    var body: some View {
        NotchDeckStack {
            ForEach(deck.mounted, id: \.self) { tab in
                let role = deck.role(tab)
                let layout = deck.layouts[tab] ?? NotchDeckLayout(width: 0, wide: false)
                NotchDeckPage(tab: tab, role: role, layout: layout) { content(tab, layout) }
                    .equatable()
                    .modifier(NotchDeckShift(steps: steps(tab, role), visible: role == .front && !veiled))
                    .environment(\.notchPageShown, role == .front)
                    .disabled(role != .front)
                    .allowsHitTesting(role == .front)
                    .accessibilityHidden(role != .front)
                    .zIndex(role == .front ? 2 : (role == .leaving ? 1 : 0))
                    .layoutValue(key: NotchDeckRoleKey.self, value: role)
                    // The first show of a tab arrives as the page always did.
                    .transition(.notchPage(slide))
            }
        }
    }

    private func steps(_ tab: NotchTab, _ role: NotchDeckRole) -> Double {
        switch role {
        case .front: return 0
        case .leaving: return slide.motion.removal
        // Where it would arrive from if it came forward next, so the change
        // that brings it starts from the side that change slides from.
        case .hidden: return NotchPage.motion(from: page, to: .tab(tab)).insertion
        }
    }
}
