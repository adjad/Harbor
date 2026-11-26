import Foundation

// The text side of MarkdownView, with no SwiftUI in it, so the standalone
// checks can compile it without SwiftUI's macro plugins.

/// Which links in an answer stay clickable. An answer can repeat text Harbor read
/// (an email, a web page, a file), and a click shows only the label, so a link
/// may open a web page and nothing else: never a Shortcut, a file, a call or a
/// share (`shortcuts:`, `file:`, `tel:`, `smb:` …), which Harbor itself would
/// only do behind a confirmation. A label that reads as one site's address
/// while the link opens another is not a link either. The label always stays,
/// as plain text.
enum MarkdownLinks {
    /// http or https with a host; nothing else opens from an answer.
    static func mayOpen(_ url: URL) -> Bool {
        guard let scheme = url.scheme?.lowercased(), scheme == "http" || scheme == "https" else { return false }
        return !(url.host ?? "").isEmpty
    }

    /// `text` with the link taken off every label that may not open. Untouched
    /// (the same value) when every link is fine.
    static func safe(_ text: AttributedString) -> AttributedString {
        // One link can span several runs (bold inside its label): judge the whole label.
        var links: [(url: URL, label: String, range: Range<AttributedString.Index>)] = []
        for run in text.runs {
            guard let url = run.link else { continue }
            let piece = String(text[run.range].characters)
            if let last = links.last, last.url == url, last.range.upperBound == run.range.lowerBound {
                links[links.count - 1] = (url: url, label: last.label + piece,
                                          range: last.range.lowerBound..<run.range.upperBound)
            } else {
                links.append((url: url, label: piece, range: run.range))
            }
        }
        let refused = links.filter { !mayOpen($0.url) || labelNamesAnotherSite($0.label, destination: $0.url) }
        guard !refused.isEmpty else { return text }
        var out = AttributedString()
        for run in text.runs {
            var piece = AttributedString(text[run.range])
            if refused.contains(where: { $0.range.lowerBound <= run.range.lowerBound && run.range.upperBound <= $0.range.upperBound }) {
                piece.link = nil
            }
            out.append(piece)
        }
        return out
    }

    /// True when the label reads as a web address on a different site than the
    /// link opens: an address spelled out anywhere in it ("https://…", "www.…"),
    /// or a label that is nothing but a domain name ("accounts.example.com").
    /// A subdomain of the site shown is the same site. An ordinary label
    /// ("Open in Maps", a page title) never counts.
    static func labelNamesAnotherSite(_ label: String, destination: URL) -> Bool {
        let actual = site(destination.host ?? "")
        let words = label.split(whereSeparator: { $0.isWhitespace }).map(String.init)
        for word in words {
            guard let shown = shownSite(word, bareDomain: words.count == 1) else { continue }
            if shown.isEmpty || !(actual == shown || actual.hasSuffix("." + shown)) { return true }
        }
        return false
    }

    /// The site a word names when it reads as a web address; nil when it does
    /// not read as one; "" when it reads as one but names no plain host. A word
    /// with no "http://", "https://" or "www." counts only when `bareDomain`.
    static func shownSite(_ word: String, bareDomain: Bool) -> String? {
        var rest = word.lowercased().trimmingCharacters(in: CharacterSet(charactersIn: "()[]{}<>\"'“”‘’.,;:!?…"))
        var explicit = false
        if rest.hasPrefix("https://") { rest.removeFirst(8); explicit = true }
        else if rest.hasPrefix("http://") { rest.removeFirst(7); explicit = true }
        var host = String(rest.prefix(while: { $0 != "/" && $0 != "?" && $0 != "#" }))
        // "name@host" without a scheme is an email address, not a web address.
        if host.contains("@") { return explicit ? "" : nil }
        if let colon = host.lastIndex(of: ":") {
            let port = host[host.index(after: colon)...]
            if !port.isEmpty && port.allSatisfy({ $0.isASCII && $0.isNumber }) { host = String(host[..<colon]) }
        }
        while host.hasSuffix(".") { host.removeLast() }
        if host.hasPrefix("www.") { explicit = true }
        if !explicit {
            guard bareDomain else { return nil }
            // A bare word: only something shaped like a domain name, and not a file name.
            let parts = host.split(separator: ".", omittingEmptySubsequences: false)
            guard parts.count >= 2, let last = parts.last,
                  parts.allSatisfy({ part in !part.isEmpty && part.allSatisfy({ $0.isLetter || $0.isNumber || $0 == "-" }) }),
                  (2...24).contains(last.count), last.allSatisfy({ $0.isLetter }),
                  !fileEndings.contains(String(last))
            else { return nil }
        }
        return site(host)
    }

    private static func site(_ host: String) -> String {
        var out = host.lowercased()
        while out.hasSuffix(".") { out.removeLast() }
        if out.hasPrefix("www.") { out.removeFirst(4) }
        return out
    }

    /// Endings that make a bare word a file name ("README.md", "report.pdf"),
    /// which a link may be labelled with wherever it goes.
    private static let fileEndings: Set<String> = [
        "md", "txt", "rtf", "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "pages", "numbers", "key", "csv", "json",
        "xml", "yml", "yaml", "toml", "plist", "log", "html", "htm", "css", "js", "ts", "py", "rb", "go", "rs", "sh",
        "swift", "java", "cpp", "png", "jpg", "jpeg", "gif", "heic", "svg", "mov", "mp4", "mp3", "wav", "zip", "dmg",
        "pkg", "exe", "app",
    ]
}

/// Inline Markdown for MarkdownView: a paragraph, list item or table cell.
enum MarkdownInline {
    /// A paragraph, list item or table cell as styled text, remembered by its
    /// source. Block parsing was already cached, but this ran again for every
    /// piece of every reply each time the surface redrew (a key typed in Ask,
    /// a streamed token, a voice level), and it is the costliest step: an
    /// Ask chat of a few replies spent most of each redraw here. While a reply
    /// streams, only its last piece is new, so the pieces before it hit too.
    static func cached(_ s: String) -> AttributedString {
        let key = s as NSString
        if let hit = inlineCache.object(forKey: key) { return hit.text }
        let made = parsed(s)
        inlineCache.setObject(InlineText(made), forKey: key, cost: key.length)
        return made
    }

    /// The uncached parse (cached's miss, and the checks' reference).
    static func parsed(_ s: String) -> AttributedString {
        let opts = AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        guard let parsed = try? AttributedString(markdown: s, options: opts) else { return AttributedString(s) }
        // Paragraphs, list items and table cells all come through here.
        return MarkdownLinks.safe(parsed)
    }

    private final class InlineText {
        let text: AttributedString
        init(_ text: AttributedString) { self.text = text }
    }
    private static let inlineCache: NSCache<NSString, InlineText> = {
        let cache = NSCache<NSString, InlineText>()
        cache.countLimit = 4096
        cache.totalCostLimit = 4_000_000   // characters of source text
        return cache
    }()
}
