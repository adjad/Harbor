// Contract for swarm #4, #5, #11 (BackendRestartPolicy in BackendManager.swift), #10
// (HarborClient.postWithOneRetry) and #32 (ResultPost.swift). Pure: no service,
// no network, no process.
import Foundation

@main
struct SwarmLifecycleChecks {
    static func main() async {
        var count = 0
        func check(_ condition: Bool, _ message: String) {
            count += 1
            if !condition { fputs("FAIL: \(message)\n", stderr); exit(1) }
        }

        // #4: 1 s, 5 s, 15 s, then stop and say why.
        check(BackendRestartPolicy.delay(afterCrashes: 1) == 1, "first crash: 1 s")
        check(BackendRestartPolicy.delay(afterCrashes: 2) == 5, "second: 5 s")
        check(BackendRestartPolicy.delay(afterCrashes: 3) == 15, "third: 15 s")
        check(BackendRestartPolicy.delay(afterCrashes: 4) == nil, "fourth: give up")
        check(BackendRestartPolicy.delay(afterCrashes: 0) == nil, "no crash, no restart")
        let now = Date(timeIntervalSince1970: 1_000_000)
        let crashes = [now.addingTimeInterval(-BackendRestartPolicy.window - 1), now.addingTimeInterval(-30),
                       now.addingTimeInterval(5)]
        check(BackendRestartPolicy.recent(crashes, now: now) == [now.addingTimeInterval(-30)],
              "only crashes inside the window count (and none from the future)")
        let reason = BackendRestartPolicy.gaveUpReason(status: 9, logTail: "example last line")
        check(reason.contains("exit status 9") && reason.hasSuffix("Last log line: example last line")
              && reason.contains("Retry"), "the reason says what happened and what to do: \(reason)")
        check(!BackendRestartPolicy.gaveUpReason(status: 1, logTail: nil).contains("Last log line"), "no tail, no line")

        // #11: a slow or missing lsof isn't a credential problem.
        check(!BackendRestartPolicy.receiptFailureQuarantines(BackendCredentials.Failure.unavailable),
              "an unavailable receipt is just not published")
        check(BackendRestartPolicy.receiptFailureQuarantines(BackendCredentials.Failure.quarantined),
              "a credential mismatch still stops the backend")

        // #10: the approval answer reports a 2xx and tries a transport error once more.
        struct Down: Error {}
        var calls = 0
        check(await HarborClient.postWithOneRetry(pause: 0) { calls += 1; return 200 } && calls == 1, "delivered at once")
        calls = 0
        check(await HarborClient.postWithOneRetry(pause: 0) {
            calls += 1
            if calls == 1 { throw Down() }
            return 204
        } && calls == 2, "a transport error is tried once more")
        calls = 0
        check(!(await HarborClient.postWithOneRetry(pause: 0) { calls += 1; throw Down() }) && calls == 2,
              "two transport errors: not delivered, so the card comes back")
        calls = 0
        check(!(await HarborClient.postWithOneRetry(pause: 0) { calls += 1; return 409 }) && calls == 1,
              "a refusal is the service's answer: not retried")

        // #32: the result post is checked and retried on transport errors and 5xx.
        var slept: [TimeInterval] = []
        calls = 0
        check(await ResultPost.deliver(sleep: { slept.append($0) }) {
            calls += 1
            if calls == 1 { throw Down() }
            return calls == 2 ? 503 : 200
        } && calls == 3 && slept == ResultPost.retryPauses, "transport error, then 503, then delivered")
        calls = 0; slept = []
        check(!(await ResultPost.deliver(sleep: { slept.append($0) }) { calls += 1; return 422 })
              && calls == 1 && slept.isEmpty, "a 4xx is not posted again")
        calls = 0; slept = []
        check(!(await ResultPost.deliver(sleep: { slept.append($0) }) { calls += 1; throw Down() })
              && calls == ResultPost.retryPauses.count + 1, "bounded: gives up after the last pause")
        check(ResultPost.retries(status: nil) && ResultPost.retries(status: 0) && ResultPost.retries(status: 500)
              && !ResultPost.retries(status: 200) && !ResultPost.retries(status: 404), "what is retried")

        print("\(count) swarm lifecycle checks passed")
    }
}
