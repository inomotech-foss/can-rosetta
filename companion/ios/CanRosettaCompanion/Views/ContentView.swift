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
