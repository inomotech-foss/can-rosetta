import SwiftUI

/// 03 — Recording. The HAL-9000 eye pulses while the drive records; the big
/// mono timer and the stats card are wired live to `RecordingController` and
/// `EdgeConnection`. "Stop recording" ends the drive.
struct RecordingView: View {
    @EnvironmentObject private var controller: RecordingController
    @EnvironmentObject private var connection: EdgeConnection
    @EnvironmentObject private var flow: DriveFlowModel

    private var standalone: Bool { flow.mode == .standalone }
    private var edgeUp: Bool { connection.connectionState == .connected }

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(spacing: 20) {
                    topBar
                    HalEye().padding(.top, 6)
                    Text(Fmt.hms(controller.elapsed))
                        .font(.system(size: 46, weight: .semibold, design: .monospaced))
                        .foregroundStyle(Theme.text)
                        .monospacedDigit()
                    Text(caption)
                        .font(.subheadline)
                        .foregroundStyle(captionColor)
                        .multilineTextAlignment(.center)
                    statsCard
                }
                .frame(maxWidth: .infinity)
                .padding(.horizontal, 20)
                .padding(.top, 8)
                .padding(.bottom, 12)
            }
            PrimaryButton(title: "Stop recording", background: Theme.red) {
                Task { await flow.stopRecording(controller: controller, connection: connection) }
            }
            .padding(.horizontal, 20)
            .padding(.bottom, 8)
        }
    }

    // MARK: Top bar

    private var topBar: some View {
        HStack {
            StatusPill(text: "REC", dotColor: Theme.red, fill: Theme.redFill, fg: Theme.redLight,
                       blinkingDot: true)
            Spacer()
            VStack(alignment: .trailing, spacing: 3) {
                Text(String(controller.sessionId.prefix(13)) + "…")
                    .font(.mono(.caption)).foregroundStyle(Theme.textSecondary)
                Text(linkLabel)
                    .font(.system(.caption2, weight: .semibold))
                    .foregroundStyle(linkColor)
            }
        }
    }

    private var linkLabel: String {
        if standalone { return "phone only" }
        return edgeUp ? "edge link ✓" : "phone-only"
    }

    private var linkColor: Color {
        if standalone { return Theme.textMuted }
        return edgeUp ? Theme.green : Theme.amber
    }

    private var caption: String {
        if standalone { return "recording locally — phone only" }
        return edgeUp ? "all systems fully operational"
                      : "edge link down — recording locally"
    }

    private var captionColor: Color {
        if standalone { return Theme.textSecondary }
        return edgeUp ? Theme.textSecondary : Theme.amber
    }

    // MARK: Stats

    private var statsCard: some View {
        FlowCard(padding: 6) {
            VStack(spacing: 0) {
                row("IMU", imuValue)
                RowSeparator(leadingInset: 12)
                row("GPS", gpsValue)
                if controller.filmDashboard {
                    RowSeparator(leadingInset: 12)
                    row("Dashboard video", videoValue)
                }
                if !standalone {
                    RowSeparator(leadingInset: 12)
                    row("AutoPi · can0", canValue)
                }
            }
        }
    }

    private func row(_ label: String, _ value: String) -> some View {
        InfoRow(label: label, value: value).padding(.horizontal, 12)
    }

    private var imuValue: String {
        controller.motionCount > 0
            ? String(format: "%.0f Hz · %d samples", controller.imuRateHz, controller.motionCount)
            : "—"
    }

    private var gpsValue: String {
        guard controller.locationCount > 0, let acc = controller.gpsHorizontalAccuracy else { return "—" }
        return String(format: "±%.0f m · %d fixes", acc, controller.locationCount)
    }

    private var videoValue: String {
        let frames = controller.videoFrameCount
        guard frames > 0 else { return "—" }
        let fps = controller.elapsed > 0 ? Double(frames) / controller.elapsed : 0
        return String(format: "%.0f fps · %d frames", fps, frames)
    }

    private var canValue: String {
        guard edgeUp else { return "—" }
        return "\(connection.frames) frames · load —"
    }
}

/// The pulsing HAL-9000 eye: a radial red core with an upper-left white
/// highlight, breathing on a ~2.4 s red glow.
struct HalEye: View {
    @State private var pulse = false
    var body: some View {
        ZStack {
            Circle()
                .fill(RadialGradient(
                    gradient: Gradient(colors: [Theme.redLight, Theme.red, Color(hex: 0x7F1D1D), Color(hex: 0x2A0606)]),
                    center: UnitPoint(x: 0.38, y: 0.36), startRadius: 2, endRadius: 92))
            Circle()
                .fill(RadialGradient(
                    gradient: Gradient(colors: [Color.white.opacity(0.95), Color.white.opacity(0.0)]),
                    center: .center, startRadius: 0, endRadius: 16))
                .frame(width: 32, height: 32)
                .offset(x: -26, y: -26)
            Circle().strokeBorder(Color.black.opacity(0.45), lineWidth: 2)
        }
        .frame(width: 132, height: 132)
        .scaleEffect(pulse ? 1.06 : 1.0)
        .shadow(color: Theme.red.opacity(pulse ? 0.9 : 0.35), radius: pulse ? 36 : 14)
        .onAppear {
            withAnimation(.easeInOut(duration: 1.2).repeatForever(autoreverses: true)) { pulse = true }
        }
        .accessibilityLabel("Recording indicator")
    }
}

#Preview {
    RecordingView()
        .environmentObject(RecordingController())
        .environmentObject(EdgeConnection())
        .environmentObject(DriveFlowModel())
        .preferredColorScheme(.dark)
}
