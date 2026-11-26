"""Swarm #4, #5, #11, #10, #32, #35: how the app-side fixes are wired. The pure
parts are checked in tests/SwarmLifecycleChecks.swift and AssistantDeliveryChecks.swift.
Source checks only."""
from __future__ import annotations

from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app" / "Sources" / "HarborApp"


def _body(text: str, signature: str) -> str:
    start = text.index(signature)
    open_at = text.index("{", start)
    depth = 0
    for i in range(open_at, len(text)):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0:
            return text[open_at:i + 1]
    raise AssertionError(signature)


def test_backend_restarts_after_a_crash_and_restart_waits_for_a_start_in_flight():
    manager = (APP / "BackendManager.swift").read_text()
    ended = _body(manager, "private func launchEnded(")
    assert "serviceEnded(pid: pid, status: status, logURL: logURL, after: offset)" in ended
    crash = _body(manager, "private func serviceEnded(")
    # Only our own live child, after it answered; a stop on purpose detaches it first.
    assert "process?.processIdentifier == pid" in crash and "everReachable" in crash
    assert "!credentialState.blocked" in crash and "!portConflict" in crash
    assert "BackendRestartPolicy.delay(afterCrashes: crashes.count)" in crash
    assert "lifecycle == self.lifecycle, self.process == nil" in crash
    assert "name: .harborBackendStartFailed" in crash
    restart = _body(manager, "func restart() async")
    assert restart.index("await stopAndWait()") < restart.index("await finishStartInFlight()") < restart.index(
        "await startIfNeeded()")
    start = _body(manager, "private func startIfNeeded(freshRecovery: Bool) async")
    assert start.index("guard lifecycle == self.lifecycle else { return }") < start.index("try proc.run()")
    # #11: lsof runs off the main actor; a slow one no longer quarantines a healthy backend.
    assert "Task.detached(priority: .userInitiated)" in start and "Self.publishRuntimeReceipt(" in start
    assert "BackendRestartPolicy.receiptFailureQuarantines(error)" in start


def test_a_lost_approval_brings_the_card_back_and_results_are_retried():
    overlay = (APP / "OverlayModel.swift").read_text()
    resolve = _body(overlay, "func resolve(_ approved: Bool, scope: String = \"once\")")
    assert "let delivered = await client.approve(" in resolve
    assert "self.pending = p" in resolve and "self.phase = .confirming" in resolve
    client = (APP / "HarborClient.swift").read_text()
    approve = _body(client, "func approve(sessionId: String")
    assert "Self.postWithOneRetry" in approve and "_ = try? await URLSession.shared.data(for: req)" not in approve
    sender = (APP / "OutboundSender.swift").read_text()
    post = _body(sender, "private static func post(actionId: String")
    assert "ResultPost.deliver" in post and "dataTask(with: req).resume()" not in post
    delivery = (APP / "AssistantDelivery.swift").read_text()
    init = _body(delivery, "    init(path: URL")
    assert "readable = Self.setAside(path)" in init and "readable = false" not in init
