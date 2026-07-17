import SwiftUI
import CoreLocation

/// 02 — Pre-flight. A live checklist. The primary action is blocked while a
/// blocking check fails; the mount check is advisory but gates the button
/// ("enables itself when the cradle stops rattling").
struct PreflightView: View {
    @EnvironmentObject private var controller: RecordingController
    @EnvironmentObject private var connection: EdgeConnection
    @EnvironmentObject private var flow: DriveFlowModel

    // Re-evaluates non-@Published values (camera auth, free disk) periodically.
    @State private var refresh = 0
    private let ticker = Timer.publish(every: 1.5, on: .main, in: .common).autoconnect()

    private var freeBytes: Int64? { _ = refresh; return RecordingController.freeDiskBytes() }
    private var cameraAuthorized: Bool { _ = refresh; return VideoRecorder.isCameraAuthorized }

    private var storageOK: Bool { (freeBytes ?? .max) > 1_000_000_000 } // > 1 GB
    private var cameraOK: Bool { !controller.filmDashboard || cameraAuthorized }

    private var startEnabled: Bool { storageOK && cameraOK && controller.mountSteady }

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    banner
                    FlowCard(padding: 6) { checklist }
                }
                .padding(.horizontal, 20)
                .padding(.top, 8)
                .padding(.bottom, 12)
            }
            VStack(spacing: 8) {
                PrimaryButton(title: "Start recording", enabled: startEnabled) {
                    Task { await flow.startRecording(controller: controller, connection: connection) }
                }
                Text(startEnabled ? "Both recorders armed."
                                  : "Enables itself when the cradle stops rattling.")
                    .font(.caption).foregroundStyle(Theme.textMuted)
            }
            .padding(.horizontal, 20)
            .padding(.bottom, 8)
        }
        .onReceive(ticker) { _ in refresh &+= 1 }
    }

    private var banner: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("DON'T PANIC")
                .font(.system(size: 28, weight: .heavy))
                .foregroundStyle(Theme.indigoLight)
            Text("A quick pre-flight before you pull away. Everything below is checked live.")
                .font(.system(size: 13))
                .foregroundStyle(Theme.textSecondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .background(Theme.indigoSubtleFill, in: RoundedRectangle(cornerRadius: Theme.cardRadius, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Theme.cardRadius, style: .continuous)
            .strokeBorder(Theme.indigoSubtleBorder, lineWidth: 1))
    }

    // MARK: Checklist

    @ViewBuilder private var checklist: some View {
        let items = checks
        VStack(spacing: 0) {
            ForEach(Array(items.enumerated()), id: \.offset) { idx, item in
                CheckRow(title: item.title, detail: item.detail, status: item.status)
                    .padding(.horizontal, 12)
                if idx < items.count - 1 { RowSeparator(leadingInset: 12) }
            }
        }
    }

    private struct Check { let title: String; let detail: String; let status: CheckStatus }

    private var checks: [Check] {
        var out: [Check] = []

        // Paired with AutoPi — only relevant when paired (dropped in standalone).
        if flow.mode == .paired {
            let paired = connection.connectionState == .connected
            out.append(Check(
                title: "Paired with AutoPi",
                detail: paired ? "edge online · \(hostOnly)" : "not paired — phone-only recording",
                status: paired ? .ok : .warn))
        }

        // GPS fix
        out.append(gpsCheck)

        // Clocks pinned — an edge-clock concept; dropped in standalone.
        if flow.mode == .paired {
            if let offset = connection.timeOffset {
                out.append(Check(title: "Clocks pinned",
                                 detail: String(format: "%+.0f ms · Cristian", offset * 1000),
                                 status: .ok))
            } else {
                out.append(Check(title: "Clocks pinned", detail: "no sync yet · Cristian", status: .warn))
            }
        }

        // Motion permission
        out.append(Check(title: "Motion permission",
                         detail: controller.isMotionAvailable ? "granted" : "unavailable on this device",
                         status: controller.isMotionAvailable ? .ok : .warn))

        // Storage
        if let bytes = freeBytes {
            out.append(Check(title: "Storage", detail: Fmt.gbFree(bytes),
                             status: storageOK ? .ok : .warn))
        } else {
            out.append(Check(title: "Storage", detail: "unknown", status: .warn))
        }

        // Camera (only if filming the dash)
        if controller.filmDashboard {
            out.append(Check(title: "Camera sees the dash",
                             detail: cameraAuthorized ? "ready" : "camera not authorized",
                             status: cameraAuthorized ? .ok : .warn))
        }

        // Phone mounted (IMU vibration)
        out.append(mountCheck)
        return out
    }

    private var gpsCheck: Check {
        switch controller.locationAuthorization {
        case .denied, .restricted:
            return Check(title: "GPS fix", detail: "location denied", status: .warn)
        case .notDetermined:
            return Check(title: "GPS fix", detail: "awaiting permission", status: .warn)
        default:
            if let acc = controller.gpsHorizontalAccuracy {
                return Check(title: "GPS fix", detail: String(format: "±%.0f m", acc), status: .ok)
            }
            return Check(title: "GPS fix", detail: "acquiring fix…", status: .warn)
        }
    }

    private var mountCheck: Check {
        if controller.mountSteady {
            return Check(title: "Phone mounted", detail: "steady", status: .ok)
        }
        return Check(title: "Phone mounted", detail: "vibration high — snug the cradle", status: .warn)
    }

    private var hostOnly: String {
        connection.host
            .replacingOccurrences(of: "http://", with: "")
            .replacingOccurrences(of: "https://", with: "")
    }
}

#Preview {
    PreflightView()
        .environmentObject(RecordingController())
        .environmentObject(EdgeConnection())
        .environmentObject(DriveFlowModel())
        .preferredColorScheme(.dark)
}
