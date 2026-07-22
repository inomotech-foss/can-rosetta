import SwiftUI

/// Host for the CAN-Rosetta Companion drive flow. Owns the flow view-model and
/// switches between the five screens over the shared dark page background.
struct ContentView: View {
    @EnvironmentObject private var controller: RecordingController
    @EnvironmentObject private var connection: EdgeConnection
    @StateObject private var flow = DriveFlowModel()

    var body: some View {
        ZStack {
            Theme.pageBg.ignoresSafeArea()

            switch flow.phase {
            case .pair:
                PairView().transition(pageTransition)
            case .preflight:
                PreflightView().transition(pageTransition)
            case .recording:
                RecordingView().transition(pageTransition)
            case .syncMarker:
                SyncMarkerView().transition(pageTransition)
            case .handoff:
                HandoffView().transition(pageTransition)
            }
        }
        .environmentObject(flow)
        .preferredColorScheme(.dark)
        .tint(Theme.indigo)
        // The idle status widget deep-links here via `canrosetta://record`
        // (RecordingStatusWidget uses widgetURL; the scheme is declared in
        // Info.plist). Bring the app forward and jump to the start of the drive
        // flow — navigation only, so starting still runs the pre-flight flow.
        // This lives on the flow host (not the App scene) because that is where
        // DriveFlowModel is owned; onOpenURL fires for the active scene either way.
        .onOpenURL { url in
            guard url.scheme == "canrosetta",
                  url.host == "record" || url.path == "/record" else { return }
            flow.goToStart()
        }
    }

    private var pageTransition: AnyTransition {
        .asymmetric(
            insertion: .move(edge: .trailing).combined(with: .opacity),
            removal: .move(edge: .leading).combined(with: .opacity)
        )
    }
}

/// A simple label / value row used by the advanced `RemoteControlView`.
struct StatusRow: View {
    let label: String
    let value: String
    var body: some View {
        HStack {
            Text(label)
            Spacer()
            Text(value)
                .foregroundStyle(.secondary)
                .font(.system(.body, design: .monospaced))
        }
    }
}

/// Wraps `UIActivityViewController` so the exported zip can be shared to Files,
/// AirDrop, etc.
struct ShareSheet: UIViewControllerRepresentable {
    let items: [Any]
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }
    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}

#Preview {
    ContentView()
        .environmentObject(RecordingController())
        .environmentObject(EdgeConnection())
}
