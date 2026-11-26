import Combine
import SwiftUI

/// The Ask field's text (OverlayModel.input), in an object of its own so a key
/// typed redraws only what reads it (AskInputScope), not the whole panel.
@MainActor
final class AskInputText: ObservableObject {
    @Published var text = ""

    /// Whether the text is empty once spaces and line breaks are trimmed: the
    /// one thing about it the rest of the panel reads ("Try another" hides
    /// while something is typed).
    static func isBlank(_ text: String) -> Bool {
        text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    /// Fires when the text goes from blank to not blank or back, never for a
    /// key that leaves that the same; not for the value it starts with.
    var blankChanges: AnyPublisher<Bool, Never> {
        $text.map(Self.isBlank).removeDuplicates().dropFirst().eraseToAnyPublisher()
    }
}

/// Draws `content` again whenever the Ask field's text changes, and nothing
/// else does: the panel around it observes OverlayModel, which sends a change
/// only when the text's blankness does (OverlayModel.input).
struct AskInputScope<Content: View>: View {
    @ObservedObject var text: AskInputText
    @ViewBuilder let content: () -> Content
    var body: some View { content() }
}
