import Combine
import Foundation

// Ask's empty-state prompts built from the person's own data: the service's
// /assistant/ask/suggestions answers with up to four questions, each offered only
// when the cache behind it is recent enough to answer it (service/assistant/
// ask_suggestions.py). Nothing is shown until the service has answered; an answer
// with none leaves Ask's general starters (AskStarters) in place.

enum AskSuggestionPrompts {
    static let limit = 4

    /// What the service said. `nudged` lists sources it asked the app to read again
    /// because they had no usable cache, so the prompts are worth asking for once more.
    struct Answer: Equatable {
        var prompts: [String]
        var nudged: [String]
    }

    /// The answer's prompts, trimmed, without blanks or repeats, at most `limit`, and
    /// never a slash command (a prompt from the service is never a command, least of all the reset).
    static func parse(_ data: Data) -> Answer? {
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let raw = object["prompts"] as? [Any] else { return nil }
        var seen = Set<String>()
        var prompts: [String] = []
        for case let text as String in raw {
            let clean = text.split(whereSeparator: \.isWhitespace).joined(separator: " ")
            guard !clean.isEmpty, clean.count <= 120, !clean.hasPrefix("/"), !NotchCommand.isReset(clean),
                  seen.insert(clean.lowercased()).inserted else { continue }
            prompts.append(clean)
            if prompts.count == limit { break }
        }
        return Answer(prompts: prompts, nudged: (object["nudged"] as? [String]) ?? [])
    }

    /// What Ask's empty state shows. Nothing while the service hasn't answered (no
    /// placeholder prompts that then change); the person's own prompts once it has;
    /// the general starters only when it answered with none.
    static func shown(personal: [String]?, general: [String]) -> [String] {
        guard let personal else { return [] }
        return personal.isEmpty ? general : personal
    }
}

@MainActor
final class AskSuggestions: ObservableObject {
    static let shared = AskSuggestions()

    /// nil until the service has answered once this launch; the last answer after that.
    @Published private(set) var prompts: [String]?

    /// How long after a nudge to ask again, for the app's read to land in the service.
    var settleDelay: UInt64 = 8_000_000_000

    /// Replaced by checks; the app reads the local service.
    var load: (URLRequest) async -> (Data, Int)? = { request in
        guard let (data, response) = try? await URLSession.shared.data(for: request),
              let status = (response as? HTTPURLResponse)?.statusCode else { return nil }
        return (data, status)
    }

    private var generation = 0

    /// Ask the service for the prompts now. Called each time Ask's empty state appears,
    /// so a calendar, inbox or note that arrived since shows up. A newer call wins; a
    /// failed one keeps what was known, or settles on the general starters when nothing was.
    func refresh() async {
        generation += 1
        let mine = generation
        guard let first = await fetch(nudge: true) else {
            // No answer at all (service starting, an older service, a timeout): with nothing
            // known yet, Ask shows its general starters rather than an empty state. A later
            // refresh brings the person's own prompts.
            if mine == generation, prompts == nil { apply([]) }
            return
        }
        guard mine == generation else { return }
        apply(first.prompts)
        guard !first.nudged.isEmpty else { return }
        // The app was asked to read a source that had nothing cached (right after setup, say).
        try? await Task.sleep(nanoseconds: settleDelay)
        guard mine == generation, let again = await fetch(nudge: false), mine == generation else { return }
        apply(again.prompts)
    }

    private func apply(_ value: [String]) {
        if prompts != value { prompts = value }
    }

    private func fetch(nudge: Bool) async -> AskSuggestionPrompts.Answer? {
        var url = HarborClient.baseURL.appendingPathComponent("assistant/ask/suggestions")
        if nudge { url = url.appending(queryItems: [URLQueryItem(name: "nudge", value: "1")]) }
        var request = URLRequest(url: url)
        request.timeoutInterval = 5
        guard let (data, status) = await load(request), status == 200 else { return nil }
        return AskSuggestionPrompts.parse(data)
    }
}
