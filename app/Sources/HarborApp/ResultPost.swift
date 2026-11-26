import Foundation

/// Posting an action's result back to the service, checked and retried (swarm #32:
/// OutboundSender's post was fire-and-forget, and a lost one left the service
/// waiting 45 s and calling a send that went out unconfirmed). Pure: `send` does the
/// request and returns its HTTP status.
enum ResultPost {
    /// The waits before each retry.
    static let retryPauses: [TimeInterval] = [0.5, 1.5]

    /// Whether a reply is worth posting again: a transport error (nil), no status, or a
    /// 5xx. A 2xx is done and a 4xx is the service's own answer.
    static func retries(status: Int?) -> Bool {
        guard let status else { return true }
        return status == 0 || (500..<600).contains(status)
    }

    /// Posts with `send`, retrying after each of `pauses`. True once a 2xx came back.
    static func deliver(pauses: [TimeInterval] = retryPauses,
                        sleep: (TimeInterval) async -> Void = {
                            try? await Task.sleep(nanoseconds: UInt64($0 * 1_000_000_000))
                        },
                        _ send: () async throws -> Int) async -> Bool {
        for attempt in 0...pauses.count {
            let status: Int? = try? await send()
            if let status, (200..<300).contains(status) { return true }
            guard retries(status: status), attempt < pauses.count else { return false }
            await sleep(pauses[attempt])
        }
        return false
    }
}
