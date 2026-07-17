import SwiftUI

/// Drives the five-screen companion flow:
/// **Pair → Pre-flight → Recording → Sync marker → Hand-off**.
///
/// It owns only navigation state; all recording/remote logic stays in
/// `RecordingController` and `EdgeConnection`, which it calls through.
@MainActor
final class DriveFlowModel: ObservableObject {

    enum Phase: Int { case pair, preflight, recording, syncMarker, handoff }

    /// Whether this drive is coordinated with an AutoPi (`.paired`) or a
    /// phone-only recording with no edge in the loop (`.standalone`). Only in
    /// `.paired` is `EdgeConnection` configured/started.
    enum PairingMode { case paired, standalone }

    @Published var phase: Phase = .pair
    @Published var mode: PairingMode = .paired
    /// Set once the driver pins a sync marker for this drive.
    @Published var markerPinned = false

    // MARK: Transitions

    /// Enter the flow paired with an AutoPi: run the edge health check and step
    /// into pre-flight.
    func confirmPairing(controller: RecordingController, connection: EdgeConnection) {
        mode = .paired
        controller.startPreflight()
        if connection.isConfigured, connection.connectionState != .connected {
            Task { await connection.checkHealth() }
        }
        withPhase(.preflight)
    }

    /// Enter the flow phone-only — no pairing, no `EdgeConnection` involvement.
    func recordStandalone(controller: RecordingController) {
        mode = .standalone
        controller.startPreflight()
        withPhase(.preflight)
    }

    /// Coordinated start when paired+connected, otherwise a phone-only recording.
    /// In `.standalone` the `EdgeConnection` is never touched.
    func startRecording(controller: RecordingController, connection: EdgeConnection) async {
        controller.stopPreflight()
        switch mode {
        case .standalone:
            controller.start()
        case .paired:
            if connection.connectionState == .connected {
                await connection.startRecording(controller: controller)
            } else {
                // Honest fallback so the flow still works without an AutoPi in reach.
                controller.start()
                connection.connect()
            }
        }
        if controller.isRecording { withPhase(.recording) }
    }

    func stopRecording(controller: RecordingController, connection: EdgeConnection) async {
        switch mode {
        case .standalone:
            controller.stop()
        case .paired:
            if connection.connectionState == .connected {
                await connection.stopRecording(controller: controller)
            } else {
                controller.stop()
            }
        }
        withPhase(markerPinned ? .handoff : .syncMarker)
    }

    func pinMarker(controller: RecordingController) {
        guard !markerPinned else { return }
        controller.addSyncMarker(kind: "brake_pulse", count: 3)
        markerPinned = true
    }

    func continueToHandoff() { withPhase(.handoff) }

    func skipMarker() { withPhase(.handoff) }

    /// Reset for a fresh drive (from the hand-off screen).
    func startAnotherDrive(controller: RecordingController) {
        controller.newSessionID()
        markerPinned = false
        mode = .paired
        withPhase(.pair)
    }

    private func withPhase(_ next: Phase) {
        withAnimation(.easeInOut(duration: 0.35)) { phase = next }
    }
}
