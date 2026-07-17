import SwiftUI

/// 04 — Sync marker. A guided "flash the brakes" step. Pinning the marker writes
/// a `brake_pulse` `sync_marker` (count 3) into the session manifest. We are
/// honest about alignment: the phone has its own IMU decel spike locally, but
/// the CAN/video offsets are computed server-side.
struct SyncMarkerView: View {
    @EnvironmentObject private var controller: RecordingController
    @EnvironmentObject private var flow: DriveFlowModel

    private var pinned: Bool { flow.markerPinned }

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    header
                    alignmentCard
                    morseNote
                }
                .padding(.horizontal, 20)
                .padding(.top, 8)
                .padding(.bottom, 12)
            }
            VStack(spacing: 10) {
                PrimaryButton(title: pinned ? "Marker pinned ✓" : "Marker pinned — drive",
                              enabled: !pinned, background: Theme.green) {
                    flow.pinMarker(controller: controller)
                }
                if pinned {
                    PrimaryButton(title: "Continue to hand-off", background: Theme.indigo) {
                        flow.continueToHandoff()
                    }
                } else {
                    Button("Skip") { flow.skipMarker() }
                        .font(.subheadline).foregroundStyle(Theme.textMuted)
                }
            }
            .padding(.horizontal, 20)
            .padding(.bottom, 8)
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
            SectionLabel(text: "Step 4 of 5")
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text("3×")
                    .font(.system(size: 44, weight: .heavy, design: .rounded))
                    .foregroundStyle(Theme.green)
                Text("Flash the brakes")
                    .font(.system(size: 24, weight: .bold))
                    .foregroundStyle(Theme.text)
            }
            Text("At a safe, stationary moment, tap the brake pedal three times in quick succession. The sharp decelerations give the server an unmistakable landmark to align the phone, IMU, video and the AutoPi's CAN log.")
                .font(.subheadline)
                .foregroundStyle(Theme.textSecondary)
        }
    }

    private var alignmentCard: some View {
        FlowCard(padding: 6) {
            VStack(spacing: 0) {
                CheckRow(title: "IMU",
                         detail: pinned ? "decel spike captured locally" : "waiting for brake pulses",
                         status: pinned ? .ok : .pending)
                    .padding(.horizontal, 12)
                RowSeparator(leadingInset: 12)
                CheckRow(title: "CAN", detail: "pending · server aligns", status: .pending)
                    .padding(.horizontal, 12)
                RowSeparator(leadingInset: 12)
                CheckRow(title: "Video", detail: "pending · server aligns", status: .pending)
                    .padding(.horizontal, 12)
            }
        }
    }

    private var morseNote: some View {
        HStack(spacing: 10) {
            Image(systemName: "waveform").foregroundStyle(Theme.indigoLight)
            Text("Three pulses read like Morse — a tidy `· · ·` the alignment pass can't miss.")
                .font(.mono(.caption)).foregroundStyle(Theme.textSecondary)
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.indigoSubtleFill, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
    }
}

#Preview {
    SyncMarkerView()
        .environmentObject(RecordingController())
        .environmentObject(DriveFlowModel())
        .preferredColorScheme(.dark)
}
