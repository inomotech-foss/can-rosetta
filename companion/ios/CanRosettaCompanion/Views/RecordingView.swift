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
                    HalEye(ax: controller.accelGX, ay: controller.accelGY).padding(.top, 6)
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

/// The pulsing HAL-9000 eye. The white highlight is a live **g-ball**: it rides
/// the IMU's user acceleration — centred at rest, sliding toward the direction
/// of acceleration (right under lateral g, up under forward g), clamped to the
/// rim. The eye still breathes on a ~2.4 s red glow.
struct HalEye: View {
    /// User acceleration in g: x = lateral (right +), y = longitudinal (up +).
    var ax: Double = 0
    var ay: Double = 0

    @State private var pulse = false

    // ~60 pt per g, clamped so the highlight stays inside the eye.
    private var ballOffset: CGSize {
        let scale = 60.0
        let maxR = 42.0
        var dx = ax * scale
        var dy = -ay * scale // device-up accel moves the ball up (screen y is down)
        let mag = (dx * dx + dy * dy).squareRoot()
        if mag > maxR, mag > 0 {
            dx *= maxR / mag
            dy *= maxR / mag
        }
        return CGSize(width: dx, height: dy)
    }

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
                .offset(ballOffset)
                .animation(.easeOut(duration: 0.12), value: ballOffset)
            Circle().strokeBorder(Color.black.opacity(0.45), lineWidth: 2)
        }
        .frame(width: 132, height: 132)
        .clipShape(Circle())
        .scaleEffect(pulse ? 1.06 : 1.0)
        .shadow(color: Theme.red.opacity(pulse ? 0.9 : 0.35), radius: pulse ? 36 : 14)
        .onAppear {
            withAnimation(.easeInOut(duration: 1.2).repeatForever(autoreverses: true)) { pulse = true }
        }
        .accessibilityLabel("Recording indicator; the highlight tracks acceleration")
    }
}

#Preview {
    RecordingView()
        .environmentObject(RecordingController())
        .environmentObject(EdgeConnection())
        .environmentObject(DriveFlowModel())
        .preferredColorScheme(.dark)
}
