import Combine
import Foundation

/// What keeps the notch smooth without changing what it shows:
/// - a reply's inline Markdown is parsed once per piece of text and remembered
///   (MarkdownInline.cached, which MarkdownView draws from), and the remembered text is the same styled text;
/// - the Ask field's text sends a change to the rest of the panel only when it
///   goes blank or stops being blank (AskInputText.blankChanges), never per key.
/// Compiled with MarkdownInline and AskInputText only: no SwiftUI, so no macro
/// plugins (which a standalone gate cannot load inside Simulation QA), no window.
/// Block parsing is MarkdownChecks' (scripts/test_markdown_contract.sh).
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
            let parsed = MarkdownInline.parsed(s)
            let first = MarkdownInline.cached(s)
            let second = MarkdownInline.cached(s)
            check(first == parsed, "first cached parse equals the parse: \(s.debugDescription)")
            check(second == parsed, "remembered parse equals the parse: \(s.debugDescription)")
        }
        let refused = MarkdownInline.cached("A [shortcut](shortcuts://run-shortcut?name=Invented) is only its label.")
        check(refused.runs.allSatisfy { $0.link == nil }, "a shortcuts: link stays refused through the cache")
        let kept = MarkdownInline.cached("A [safe link](https://example.com/notes) here.")
        check(kept.runs.contains { $0.link?.host == "example.com" }, "a web link stays a link through the cache")

        // A redraw of a settled reply reads remembered text: far cheaper than parsing.
        let paragraphs = (0..<300).map {
            "Invented reply \($0): finish the **lab write-up** for section \($0 % 7), read [chapter \($0 % 12)](https://example.com/c\($0)) and *send the slides*."
        }
        func time(_ body: () -> Void) -> Double {
            let t0 = Date(); body(); return Date().timeIntervalSince(t0)
        }
        let uncached = time { for _ in 0..<3 { for p in paragraphs { _ = MarkdownInline.parsed(p) } } }
        for p in paragraphs { _ = MarkdownInline.cached(p) }
        let cached = time { for _ in 0..<3 { for p in paragraphs { _ = MarkdownInline.cached(p) } } }
        print(String(format: "inline Markdown, 900 pieces: parsed %.1f ms, remembered %.1f ms", uncached * 1000, cached * 1000))
        check(cached < uncached * 0.5, "remembered inline text costs under half a parse")

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
