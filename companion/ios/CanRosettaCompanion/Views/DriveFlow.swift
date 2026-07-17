import SwiftUI

/// Drives the five-screen companion flow:
/// **Pair → Pre-flight → Recording → Sync marker → Hand-off**.
///
/// It owns only navigation state; all recording/remote logic stays in
/// `RecordingController` and `EdgeConnection`, which it calls through.
@MainActor
final class DriveFlowModel: ObservableObject {

    enum Phase: Int { case pair, preflight, recording, syncMarker, handoff }

    @Published var phase: Phase = .pair
    /// Set once the driver pins a sync marker for this drive.
    @Published var markerPinned = false

    // MARK: Transitions

    func confirmPairing(controller: RecordingController, connection: EdgeConnection) {
        controller.startPreflight()
        if connection.isConfigured, connection.connectionState != .connected {
            Task { await connection.checkHealth() }
        }
        withPhase(.preflight)
    }

    /// Coordinated start when paired+connected, otherwise a phone-only recording
    /// (honest fallback so the flow works without an AutoPi in reach).
    func startRecording(controller: RecordingController, connection: EdgeConnection) async {
        controller.stopPreflight()
        if connection.connectionState == .connected {
            await connection.startRecording(controller: controller)
        } else {
            controller.start()
            connection.connect()
        }
        if controller.isRecording { withPhase(.recording) }
    }

    func stopRecording(controller: RecordingController, connection: EdgeConnection) async {
        if connection.connectionState == .connected {
            await connection.stopRecording(controller: controller)
        } else {
            controller.stop()
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
        withPhase(.pair)
    }

    private func withPhase(_ next: Phase) {
        withAnimation(.easeInOut(duration: 0.35)) { phase = next }
    }
}
