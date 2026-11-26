import AppKit
import SwiftUI

// The ⌘⇧F content view. It does NOT own its window — AppDelegate hosts it in
// an `OverlayPanel`, the same notch-fused reveal panel the chat assistant
// uses, so Smart Search gets the identical black surface, rounded-bottom
// notch corners, and spring-open/roll-up animation for free rather than a
// second, visually distinct floating card. The two panels are mutually
// exclusive at the notch — see AppDelegate.toggleSearch/expand.
struct SearchView: View {
    @ObservedObject var model: SearchModel
    @FocusState private var focused: Bool
    /// Reply details (Quick Settings): only then does the panel show timings.
    @AppStorage("harbor.debugMode") private var debugMode = false

    @ObservedObject private var display = DisplayGeometry.shared
    private var layout: SurfaceLayout { display.layout }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            field
            if model.needsAccessibility && !model.hasPage {
                permissionPrompt
            } else if !model.errorText.isEmpty && model.rows.isEmpty && model.answer.isEmpty {
                notice(model.errorText, symbol: "exclamationmark.triangle")
            } else {
                content
            }
        }
        // Same surface as the chat panel: on a MacBook it hangs from the notch,
        // so content sits below it; on a monitor it is a floating card.
        .padding(EdgeInsets(top: layout.contentTopInset,
                            leading: 22, bottom: 16, trailing: 22))
        .background(GeometryReader { geo in
            Color.clear.onChange(of: geo.size.height) { _, _ in model.onResize() }
        })
        .frame(width: layout.expandedWidth)
        .background(Theme.surface)
        .clipShape(layout.panelShape)
        .shadow(color: .black.opacity(0.55), radius: 20, y: 10)
        .onAppear { focused = true }
    }

    // MARK: Search field

    private var field: some View {
        VStack(spacing: 6) {
            HStack(spacing: 10) {
                Image(systemName: "sparkle.magnifyingglass")
                    .foregroundStyle(Theme.textSecondary)
                TextField("Ask about this page…", text: $model.query)
                    .textFieldStyle(.plain)
                    .font(.harbor(size: 16, weight: .regular))
                    .foregroundStyle(Theme.textPrimary)
                    .focused($focused)
                    .onChange(of: model.query) { _, _ in model.queryChanged() }
                if model.busy {
                    ProgressView().controlSize(.small).frame(width: 14, height: 14)
                        .accessibilityLabel("Searching")
                }
                if !model.rows.isEmpty {
                    Text("\(model.selection + 1)/\(model.rows.count)")
                        .font(.harbor(size: 11, design: .monospaced))
                        .foregroundStyle(Theme.textMuted)
                }
            }
            HStack(spacing: 6) {
                Text(model.status)
                    .font(.harbor(size: 10))
                    .foregroundStyle(Theme.textMuted)
                    .lineLimit(1).truncationMode(.tail)
                if model.semanticOff {
                    Text("· keyword matches only")
                        .font(.harbor(size: 10))
                        .foregroundStyle(Theme.textMuted)
                }
                if !model.indexingStatus.isEmpty {
                    Text("· \(model.indexingStatus)")
                        .font(.harbor(size: 10))
                        .foregroundStyle(Theme.textMuted)
                }
                Spacer()
                if debugMode && model.elapsedMs > 0 {
                    Text("\(model.elapsedMs)ms")
                        .font(.harbor(size: 10, design: .monospaced))
                        .foregroundStyle(Theme.textMuted)
                        .accessibilityHidden(true)
                }
            }
        }
        .padding(.bottom, 10)
    }

    // MARK: Body

    @ViewBuilder private var content: some View {
        if model.hasPage && model.query.isEmpty {
            EmptyView()
        } else {
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 10) {
                        if !model.errorText.isEmpty { partialFailureCard }
                        if model.answering { answeringCard }
                        if !model.answer.isEmpty { answerCard }
                        if model.notFound { notFoundCard }
                        resultList
                    }
                    .padding(.bottom, 4)
                }
                .frame(maxHeight: 360)
                // The arrow keys and Return move the selection from outside
                // this view (AppDelegate's key monitor), and the list is taller
                // than the panel: bring the selected row into sight.
                .onChange(of: model.selection) { _, selected in
                    let rows = model.rows
                    guard rows.indices.contains(selected) else { return }
                    withAnimation(.easeOut(duration: 0.12)) {
                        proxy.scrollTo(rows[selected].id)
                    }
                }
            }
        }
    }

    private var answeringCard: some View {
        HStack(spacing: 8) {
            ProgressView().controlSize(.mini).frame(width: 12, height: 12)
                .accessibilityLabel("Reading")
            Text(model.upgradingModel.isEmpty
                 ? "Reading the passages…"
                 : "Loading a stronger model for this…")
                .font(.harbor(size: 12))
                .foregroundStyle(Theme.textSecondary)
        }
        .padding(.vertical, 10).padding(.horizontal, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 12).fill(Theme.chipFill))
    }

    private var partialFailureCard: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: "exclamationmark.triangle")
                .foregroundStyle(Theme.textMuted)
                .accessibilityHidden(true)
            Text(model.errorText)
                .font(.harbor(size: 12))
                .foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .padding(.vertical, 10).padding(.horizontal, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 12).fill(Theme.chipFill))
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Search issue: \(model.errorText)")
    }

    private var answerCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            // An overview is inferred across a sample of the document rather
            // than lifted from one passage — say so, so it isn't read as a
            // located quotation.
            if model.answerScope == "global" {
                HStack(spacing: 5) {
                    Image(systemName: "doc.text.magnifyingglass")
                        .font(.harbor(size: 9))
                    Text("Overview · read across the document")
                        .font(.harbor(size: 9, weight: .medium))
                }
                .foregroundStyle(Theme.textMuted)
            }
            Text(model.answer)
                .font(.harbor(size: 13))
                .foregroundStyle(Theme.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)

            HStack(spacing: 6) {
                ForEach(model.citations) { c in
                    Button {
                        model.reveal(citation: c)
                    } label: {
                        Text("[\(c.n)]")
                            .font(.harbor(size: 10, design: .monospaced))
                            .padding(.horizontal, 7).padding(.vertical, 3)
                            .background(Capsule().fill(
                                model.expandedCitation == c.n
                                ? Color.white.opacity(0.18) : Theme.chipFill))
                            .overlay(Capsule().stroke(Theme.chipStroke))
                    }
                    .buttonStyle(.plain)
                    .help("Show this source")
                }
                Spacer()
                Button { model.copyAnswer() } label: {
                    Image(systemName: "doc.on.doc")
                        .font(.harbor(size: 10))
                        .foregroundStyle(Theme.textMuted)
                }
                .buttonStyle(.plain)
                .help("Copy answer with source")
                .accessibilityLabel("Copy answer with source")
            }

            if let n = model.expandedCitation,
               let c = model.citations.first(where: { $0.n == n }) {
                citationSource(c)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 12).fill(Theme.chipFill))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.chipStroke))
    }

    // Deliberately NOT "Not in this document" — that claim was usually false.
    // A span lookup failing means the matching passages didn't state an answer,
    // which is a different thing from the document not containing one, and
    // saying the stronger thing trained distrust in a feature whose whole value
    // is being believable. Offer the wider read instead of a dead end.
    /// The passage behind a citation, shown in the panel. Always available —
    /// scrolling the source app is best-effort and impossible in some viewers
    /// (Safari's PDF view among them), so this is what actually makes a
    /// citation checkable.
    private func citationSource(_ c: SearchModel.Citation) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(c.preview)
                .font(.harbor(size: 11))
                .foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
            if model.canNavigate {
                HStack { Spacer(); copyQuote(c) }
            } else {
                // On one line when there is room; otherwise the button goes below,
                // so the hint never wraps against it.
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 8) { cantJumpHint.fixedSize(); Spacer(); copyQuote(c) }
                    VStack(alignment: .leading, spacing: 2) {
                        cantJumpHint.fixedSize(horizontal: false, vertical: true)
                        HStack { Spacer(); copyQuote(c) }
                    }
                }
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color.white.opacity(0.04)))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Theme.hairline))
    }

    private var cantJumpHint: some View {
        Text("Can't jump to it in this app. Copy the quote to find it.")
            .font(.harbor(.caption))
            .foregroundStyle(Theme.textMuted)
    }

    private func copyQuote(_ c: SearchModel.Citation) -> some View {
        Button { model.copyCitation(c) } label: {
            Text("Copy quote")
                .font(.harbor(size: 10))
                .foregroundStyle(Theme.textMuted)
                .fixedSize()
                .frame(minHeight: 24).contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help("Copy this passage")
    }

    private var notFoundCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Image(systemName: "questionmark.circle").foregroundStyle(Theme.textMuted)
                    .accessibilityHidden(true)
                Text(model.canWiden
                     ? "No direct answer in the matching passages."
                     : "Couldn't answer that from this document.")
                    .font(.harbor(size: 12))
                    .foregroundStyle(Theme.textSecondary)
                Spacer()
            }
            if model.canWiden {
                Button { model.searchWholeDocument() } label: {
                    Text("Read the whole document")
                        .font(.harbor(size: 11, weight: .medium))
                        .foregroundStyle(Theme.textPrimary)
                        .padding(.horizontal, 10).padding(.vertical, 5)
                        .background(Capsule().fill(Theme.chipFill))
                        .overlay(Capsule().stroke(Theme.chipStroke))
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.vertical, 10).padding(.horizontal, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 12).fill(Theme.chipFill))
    }

    private func notice(_ text: String, symbol: String) -> some View {
        HStack(spacing: 8) {
            Image(systemName: symbol).foregroundStyle(Theme.textMuted)
            Text(text).font(.harbor(size: 12)).foregroundStyle(Theme.textSecondary)
            Spacer()
        }
        .padding(.vertical, 10).padding(.horizontal, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 12).fill(Theme.chipFill))
    }

    private var permissionPrompt: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Harbor can't read this window yet")
                .font(.harbor(size: 13, weight: .medium))
                .foregroundStyle(Theme.textPrimary)
            Text("Grant \(PrivacyNames.accessibilityWithFormerName) to search text inside other apps. Without it, "
                 + "Harbor can only read what's visible on screen.")
                .font(.harbor(size: 11))
                .foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            Button("Open \(PrivacyNames.accessibility) Settings…") {
                Permissions.requestAccessibility()
            }
            .buttonStyle(.plain)
            .font(.harbor(size: 11, weight: .medium))
            .foregroundStyle(Theme.textPrimary)
            .padding(.horizontal, 10).padding(.vertical, 5)
            .background(Capsule().fill(Theme.chipFill))
            .overlay(Capsule().stroke(Theme.chipStroke))
        }
    }

    private var resultList: some View {
        VStack(spacing: 2) {
            ForEach(Array(model.rows.enumerated()), id: \.element.id) { i, r in
                Button {
                    model.selection = i
                    model.revealSelected()
                } label: {
                    row(r, selected: i == model.selection)
                }
                .buttonStyle(.plain)
                .id(r.id)
                .accessibilityAddTraits(i == model.selection ? .isSelected : [])
            }
        }
    }

    private func row(_ r: SearchModel.Result, selected: Bool) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: icon(for: r.kind))
                .font(.harbor(size: 9))
                .foregroundStyle(Theme.textMuted)
                .frame(width: 12 * Theme.textScale)
                .padding(.top, 2)
            (Text(r.prefix).foregroundColor(Theme.textMuted)
             + Text(r.match).foregroundColor(Theme.textPrimary).bold()
             + Text(r.suffix).foregroundColor(Theme.textMuted))
                .font(.harbor(size: 11))
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 4)
            Text("\(r.line)")
                .font(.harbor(size: 9, design: .monospaced))
                .foregroundStyle(Theme.textMuted)
        }
        .padding(.vertical, 6).padding(.horizontal, 9)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 8)
            .fill(selected ? Theme.chipFill : Color.clear))
    }

    private func icon(for kind: String) -> String {
        switch kind {
        case "literal":  return "text.magnifyingglass"
        case "lexical":  return "textformat.abc"
        default:         return "sparkle"
        }
    }
}
