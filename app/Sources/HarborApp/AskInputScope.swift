import SwiftUI

/// Draws `content` again whenever the Ask field's text changes, and nothing
/// else does: the panel around it observes OverlayModel, which sends a change
/// only when the text's blankness does (OverlayModel.input).
struct AskInputScope<Content: View>: View {
    @ObservedObject var text: AskInputText
    @ViewBuilder let content: () -> Content
    var body: some View { content() }
}
