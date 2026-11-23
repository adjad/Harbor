// Compile with app/Sources/HarborApp/AskSuggestionsStore.swift, HarborClient.swift, NotchCommand.swift.
// Ask's empty-state prompts (AskSuggestionsStore.swift): how the service's answer is
// read, what is shown before, with and without prompts, and the nudge-then-ask-again
// refresh. A stub loader answers in-process: nothing reaches a socket.
import Foundation

@main
struct AskSuggestionsChecks {
    static func check(_ condition: @autoclosure () -> Bool, _ message: String) {
        precondition(condition(), message)
    }

    static func json(_ object: [String: Any]) -> Data {
        try! JSONSerialization.data(withJSONObject: object)
    }

    @MainActor
    static func run() async {
        // Reading the answer.
        let a = AskSuggestionPrompts.parse(json(["prompts": ["What's due this week?", "  What did Priya   say about the lab report? ",
                                                              "what's due this week?", "", "/resetharbor", "/ideate a thing", "a", "b", "c"],
                                                 "nudged": ["calendar"]]))!
        check(a.prompts == ["What's due this week?", "What did Priya say about the lab report?", "a", "b"],
              "trimmed, no repeats or blanks or reset phrase, at most four: \(a.prompts)")
        check(a.nudged == ["calendar"], "the nudged sources are kept")
        check(AskSuggestionPrompts.parse(json(["prompts": []]))?.prompts == [], "none is an answer")
        check(AskSuggestionPrompts.parse(json(["other": 1])) == nil && AskSuggestionPrompts.parse(Data("x".utf8)) == nil,
              "anything else is no answer")
        check(AskSuggestionPrompts.parse(json(["prompts": [String(repeating: "x", count: 200)]]))?.prompts == [],
              "a prompt too long for a chip is dropped")

        // What the empty state shows.
        let general = ["What can you do?", "Is my data private?"]
        check(AskSuggestionPrompts.shown(personal: nil, general: general) == [], "nothing before the service answers")
        check(AskSuggestionPrompts.shown(personal: ["What's due this week?"], general: general) == ["What's due this week?"],
              "the person's own prompts replace the general ones")
        check(AskSuggestionPrompts.shown(personal: [], general: general) == general, "no prompts: the general starters")

        // Refresh: asks with the nudge, and again without it after the app's read had time to land.
        let store = AskSuggestions()
        store.settleDelay = 1_000_000
        var urls: [String] = []
        var replies: [(Int, [String: Any])] = [
            (200, ["prompts": [], "nudged": ["calendar", "email"]]),
            (200, ["prompts": ["What's due this week?"], "nudged": []]),
        ]
        store.load = { request in
            urls.append(request.url!.absoluteString)
            let (status, body) = replies.isEmpty ? (500, [:]) : replies.removeFirst()
            return (json(body), status)
        }
        check(store.prompts == nil, "unknown at first")
        await store.refresh()
        check(urls.count == 2 && urls[0].hasSuffix("/assistant/ask/suggestions?nudge=1")
              && urls[1].hasSuffix("/assistant/ask/suggestions"), "nudged once, then asked again plainly: \(urls)")
        check(store.prompts == ["What's due this week?"], "the second answer is shown")

        // A failed read keeps what was known; an answer with no nudge asks once.
        urls = []
        replies = [(500, [:])]
        await store.refresh()
        check(store.prompts == ["What's due this week?"] && urls.count == 1, "a failure keeps the last prompts")
        replies = [(200, ["prompts": ["Anything in my email I still need to reply to?"], "nudged": []])]
        urls = []
        await store.refresh()
        check(store.prompts == ["Anything in my email I still need to reply to?"] && urls.count == 1, "no nudge, one request")

        // A newer refresh wins over one still waiting to ask again.
        urls = []
        store.settleDelay = 150_000_000
        replies = [(200, ["prompts": ["old"], "nudged": ["email"]]),
                   (200, ["prompts": ["new"], "nudged": []]),
                   (200, ["prompts": ["stale"], "nudged": []])]
        async let first: Void = store.refresh()
        try? await Task.sleep(nanoseconds: 30_000_000)
        await store.refresh()
        await first
        check(store.prompts == ["new"] && urls.count == 2, "the superseded refresh does not ask again: \(urls.count) \(store.prompts ?? [])")
        print("AskSuggestionsChecks passed")
    }

    static func main() async {
        await run()
    }
}
