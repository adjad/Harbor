import Combine
import Foundation
import SwiftUI

/// What keeps the notch smooth without changing what it shows:
/// - a reply's inline Markdown is parsed once per piece of text and remembered
///   (MarkdownView.inlineCached), and the remembered text is the same styled text;
/// - the Ask field's text sends a change to the rest of the panel only when it
///   goes blank or stops being blank (AskInputText.blankChanges), never per key.
/// Compiled with MarkdownView, Math, Theme and AskInputText only; no window.
@main
struct NotchSmoothnessChecks {
    @MainActor static func main() {
        var count = 0, failed = 0
        func check(_ c: Bool, _ m: String) { count += 1; if !c { failed += 1; print("FAIL: \(m)") } }

        // Cached inline text is exactly the parse, links refused or kept as before.
        let samples = [
            "Plain words only.",
            "Some **bold** and *italic* and `code`.",
            "A [safe link](https://example.com/notes) here.",
            "A [shortcut](shortcuts://run-shortcut?name=Invented) is only its label.",
            "A label naming [www.example.org](https://other.example.net) is not a link.",
            "",
            "Line one\nLine two with  two spaces",
        ]
        for s in samples {
            let parsed = MarkdownView.inlineParsed(s)
            let first = MarkdownView.inlineCached(s)
            let second = MarkdownView.inlineCached(s)
            check(first == parsed, "first cached parse equals the parse: \(s.debugDescription)")
            check(second == parsed, "remembered parse equals the parse: \(s.debugDescription)")
        }
        let refused = MarkdownView.inlineCached("A [shortcut](shortcuts://run-shortcut?name=Invented) is only its label.")
        check(refused.runs.allSatisfy { $0.link == nil }, "a shortcuts: link stays refused through the cache")
        let kept = MarkdownView.inlineCached("A [safe link](https://example.com/notes) here.")
        check(kept.runs.contains { $0.link?.host == "example.com" }, "a web link stays a link through the cache")

        // A redraw of a settled reply reads remembered text: far cheaper than parsing.
        let paragraphs = (0..<300).map {
            "Invented reply \($0): finish the **lab write-up** for section \($0 % 7), read [chapter \($0 % 12)](https://example.com/c\($0)) and *send the slides*."
        }
        func time(_ body: () -> Void) -> Double {
            let t0 = Date(); body(); return Date().timeIntervalSince(t0)
        }
        let uncached = time { for _ in 0..<3 { for p in paragraphs { _ = MarkdownView.inlineParsed(p) } } }
        for p in paragraphs { _ = MarkdownView.inlineCached(p) }
        let cached = time { for _ in 0..<3 { for p in paragraphs { _ = MarkdownView.inlineCached(p) } } }
        print(String(format: "inline Markdown, 900 pieces: parsed %.1f ms, remembered %.1f ms", uncached * 1000, cached * 1000))
        check(cached < uncached * 0.5, "remembered inline text costs under half a parse")

        // Blocks are still the same blocks.
        let reply = "Two things:\n- **first** item\n- [second](https://example.com)\n\nThen a paragraph."
        let blocks = MarkdownView(text: reply).cachedBlocks()
        check(blocks.map(\.kind) == [.paragraph, .list, .paragraph],
              "a line, a list and a paragraph parse as three blocks")

        // The Ask field's text: one change out per blank/not-blank flip.
        let input = AskInputText()
        var flips: [Bool] = []
        let sub = input.blankChanges.sink { flips.append($0) }
        var keys = 0
        let keySub = input.$text.dropFirst().sink { _ in keys += 1 }
        for typed in ["W", "Wh", "Wha", "What", "What is", "", "   ", "\n", "x", "xy"] { input.text = typed }
        check(keys == 10, "every key reaches what reads the text (\(keys))")
        check(flips == [false, true, false], "the panel hears only blank/not-blank flips: \(flips)")
        check(AskInputText.isBlank(" \n\t ") && !AskInputText.isBlank(" a "), "blank means only spaces and line breaks")
        sub.cancel(); keySub.cancel()

        print("\(count - failed)/\(count) notch smoothness checks passed")
        exit(failed == 0 ? 0 : 1)
    }
}
