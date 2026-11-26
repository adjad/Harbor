import SwiftUI
import AppKit

/// Synthetic Brief render timing (scripts/bench_brief_render.sh): open-to-content
/// for the notch Brief with a loaded day, and how often the Brief's card section
/// re-renders when something unrelated changes. Invented fixture data only; no
/// AppDelegate, live service, network or installed app.
@main
struct BriefRenderBench {
    @MainActor static func main() {
        if CommandLine.arguments.contains("--print-real-home") {
            print(FileManager.default.homeDirectoryForCurrentUser.path)
            return
        }
        let app = NSApplication.shared
        app.setActivationPolicy(.prohibited)
        Task { @MainActor in
            do {
                try await run()
                app.terminate(nil)
            } catch {
                fputs("FAIL: \(error)\n", stderr)
                exit(1)
            }
        }
        app.run()
    }

    struct Failure: Error, CustomStringConvertible { let description: String }

    @MainActor static func mount(_ view: some View) -> (NSWindow, NSHostingView<AnyView>) {
        let host = NSHostingView(rootView: AnyView(view.padding(20).frame(width: 640).background(Theme.surface)))
        let window = NSWindow(contentRect: NSRect(x: -10000, y: -10000, width: 640, height: 900),
                              styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = host
        window.orderFrontRegardless()
        return (window, host)
    }

    @MainActor static func settle(_ host: NSView) async {
        try? await Task.sleep(for: .milliseconds(15))
        host.layoutSubtreeIfNeeded()
        host.display()
    }

    @MainActor static func run() async throws {
        let zone = TimeZone(identifier: "America/Los_Angeles")!
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = zone
        let start = calendar.date(from: DateComponents(year: 2026, month: 10, day: 22))!
        let now = start.addingTimeInterval(10 * 3600 + 32 * 60)
        var readDelay: Duration = .zero
        let today = TodayModel(now: { now }, timeZoneProvider: { zone }, transport: { request in
            if readDelay > .zero { try await Task.sleep(for: readDelay) }
            let parts = URLComponents(url: request.url!, resolvingAgainstBaseURL: true)!
            if parts.path == "/assistant/upcoming" {
                return (try NotchQADriver.fixtureUpcoming(dayStart: start.timeIntervalSince1970),
                        HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!)
            }
            if parts.path != "/assistant/today" {
                return (Data(#"{"detail":"Not in fixture"}"#.utf8),
                        HTTPURLResponse(url: request.url!, statusCode: 404, httpVersion: nil, headerFields: nil)!)
            }
            let day = parts.queryItems?.first { $0.name == "day" }?.value ?? "2026-10-22"
            return (try NotchQADriver.fixturePlan(day: day, dayStart: start.timeIntervalSince1970, proactive: .ready),
                    HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!)
        })
        await today.refresh()
        guard today.currentPlan?.workspace?.proactive?.cards.isEmpty == false else {
            throw Failure(description: "the fixture Brief did not load")
        }

        // 1. Open to content: mount, lay out and draw the Brief with the day loaded.
        let model = OverlayModel()
        model.tab = .brief
        var opens: [Double] = []
        for _ in 0..<25 {
            let t0 = DispatchTime.now().uptimeNanoseconds
            let (window, host) = mount(NotchBriefView(model: model, today: today))
            window.setContentSize(host.fittingSize)
            host.layoutSubtreeIfNeeded()
            host.display()
            opens.append(Double(DispatchTime.now().uptimeNanoseconds - t0) / 1e6)
            window.close()
            try? await Task.sleep(for: .milliseconds(10))
        }
        let warm = opens.dropFirst(5).sorted()
        print(String(format: "open-to-content: first %.1f ms, median %.1f ms, p90 %.1f ms (n=%d)",
                     opens[0], warm[warm.count / 2], warm[Int(Double(warm.count) * 0.9)], warm.count))

        // 2. Re-renders of the card section when unrelated state changes.
        let (window, host) = mount(NotchBriefView(model: model, today: today))
        defer { window.close() }
        window.setContentSize(host.fittingSize)
        await settle(host)
        try? await Task.sleep(for: .milliseconds(200))
        await settle(host)
        func count(_ label: String, _ times: Int, _ change: (Int) -> Void) async {
            ProactiveBriefSection.qaBodies = 0
            let t0 = DispatchTime.now().uptimeNanoseconds
            for i in 0..<times {
                change(i)
                await settle(host)
            }
            let ms = Double(DispatchTime.now().uptimeNanoseconds - t0) / 1e6 - Double(times) * 15
            print(String(format: "%@: %d changes → %d section bodies, %.1f ms of work", label, times,
                         ProactiveBriefSection.qaBodies, ms))
        }
        await count("typing in Ask (OverlayModel.input)", 30) { model.input = "draft \($0)" }
        await count("a notice (OverlayModel.transientNotice)", 30) { model.transientNotice = $0 % 2 == 0 ? "Saved" : nil }
        await count("a planner flag (TodayModel.capturePlannerDetails)", 30) { today.capturePlannerDetails = $0 % 2 == 0 }

        // 3. The Brief opened while Day shows another day: it pins back to today
        // and reads it again (a simulated 400 ms service read). Time until the
        // Brief's cards are on screen. The earlier Brief is taken down first, so
        // only the one just opened is counted.
        host.rootView = AnyView(EmptyView())
        host.layoutSubtreeIfNeeded()
        window.close()
        do {
            today.selectDate(now.addingTimeInterval(86_400))
            await today.refresh()
            print("  before open: day \(today.day), showing today \(today.isShowingCurrentDay), follows \(today.followsToday), plan day \(today.plan?.day ?? "none")")
            readDelay = .milliseconds(400)
            ProactiveBriefSection.qaBodies = 0
            let t0 = DispatchTime.now().uptimeNanoseconds
            let (other, host2) = mount(NotchBriefView(model: model, today: today))
            other.setContentSize(host2.fittingSize)
            var waited = 0.0
            while ProactiveBriefSection.qaBodies == 0 && waited < 3000 {
                host2.layoutSubtreeIfNeeded()
                host2.display()
                if ProactiveBriefSection.qaBodies > 0 { break }
                try? await Task.sleep(for: .milliseconds(5))
                waited = Double(DispatchTime.now().uptimeNanoseconds - t0) / 1e6
            }
            let ms = Double(DispatchTime.now().uptimeNanoseconds - t0) / 1e6
            if ProactiveBriefSection.qaBodies == 0 { print("  (no cards within 3 s)") }
            print(String(format: "open from another day in Day (400 ms read): cards on screen after %.1f ms", ms))
            print("  at content: day \(today.day), showing today \(today.isShowingCurrentDay), plan day \(today.plan?.day ?? "none"), current \(today.currentPlan != nil)")
            other.close()
            readDelay = .zero
        }
        print("bench done")
    }
}
