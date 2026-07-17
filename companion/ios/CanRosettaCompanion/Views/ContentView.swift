import SwiftUI
import CoreLocation

/// Main screen: session identity, start/stop, live capture status, the
/// "film dashboard" toggle, and export/share.
struct ContentView: View {
    @EnvironmentObject private var controller: RecordingController
    @State private var showShareSheet = false

    var body: some View {
        NavigationStack {
            Form {
                sessionSection
                statusSection
                optionsSection
                exportSection
            }
            .navigationTitle("CAN-Rosetta")
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    NavigationLink {
                        RemoteControlView()
                    } label: {
                        Image(systemName: "antenna.radiowaves.left.and.right")
                    }
                }
                ToolbarItem(placement: .principal) {
                    Text(controller.isRecording ? "Recording" : "Idle")
                        .font(.headline)
                        .foregroundStyle(controller.isRecording ? .red : .secondary)
                }
            }
            .safeAreaInset(edge: .bottom) { recordButton }
            .sheet(isPresented: $showShareSheet) {
                if let url = controller.exportURL {
                    ShareSheet(items: [url])
                }
            }
        }
    }

    // MARK: - Sections

    private var sessionSection: some View {
        Section {
            HStack {
                Text("Session ID")
                Spacer()
                Button {
                    controller.newSessionID()
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .disabled(controller.isRecording)
                .buttonStyle(.borderless)
            }
            TextField("session-id", text: $controller.sessionId)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .disabled(controller.isRecording)
                .font(.system(.body, design: .monospaced))
        } header: {
            Text("Session")
        } footer: {
            Text("Must match the AutoPi's session id (agreed via QR or entered manually) so the server can merge both parts.")
        }
    }

    private var statusSection: some View {
        Section("Live status") {
            StatusRow(label: "Recording time", value: timeString(controller.elapsed))
            StatusRow(label: "IMU rate", value: String(format: "%.0f Hz", controller.imuRateHz))
            StatusRow(label: "Motion samples", value: "\(controller.motionCount)")
            StatusRow(label: "GPS fixes", value: "\(controller.locationCount)")
            StatusRow(label: "GPS fix", value: gpsFixString)
            if controller.filmDashboard {
                StatusRow(label: "Video frames", value: "\(controller.videoFrameCount)")
            }
            if controller.capturePhotos {
                StatusRow(label: "Stills", value: "\(controller.photoCount)")
            }
        }
    }

    private var optionsSection: some View {
        Section {
            Toggle("Film dashboard", isOn: $controller.filmDashboard)
                .disabled(controller.isRecording)
            Toggle("Capture stills", isOn: $controller.capturePhotos)
                .disabled(controller.isRecording)
        } footer: {
            Text("Film dashboard records the rear camera to video.mp4 (temporally dense, good for telltales/needles). Capture stills saves periodic full-resolution photos to phone/photos/ (sharper, good for OCR of small digits). Both share one camera; using them together keeps the video recording. Uses more battery and storage.")
        }
    }

    @ViewBuilder
    private var exportSection: some View {
        Section("Export") {
            if let url = controller.exportURL {
                Button {
                    showShareSheet = true
                } label: {
                    Label("Share session (\(url.lastPathComponent))", systemImage: "square.and.arrow.up")
                }
            } else {
                Text("Stop a recording to produce a shareable session archive.")
                    .foregroundStyle(.secondary)
            }
            if let error = controller.lastError {
                Text(error)
                    .foregroundStyle(.red)
                    .font(.footnote)
            }
        }
    }

    private var recordButton: some View {
        Button {
            if controller.isRecording {
                controller.stop()
            } else {
                controller.start()
            }
        } label: {
            Text(controller.isRecording ? "Stop recording" : "Start recording")
                .font(.headline)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 8)
        }
        .buttonStyle(.borderedProminent)
        .tint(controller.isRecording ? .red : .accentColor)
        .disabled(controller.sessionId.isEmpty)
        .padding()
    }

    // MARK: - Helpers

    private var gpsFixString: String {
        switch controller.locationAuthorization {
        case .denied, .restricted:
            return "Denied"
        case .notDetermined:
            return "Waiting…"
        default:
            if let acc = controller.gpsHorizontalAccuracy {
                return String(format: "±%.0f m", acc)
            }
            return "No fix"
        }
    }

    private func timeString(_ t: TimeInterval) -> String {
        let s = Int(t)
        return String(format: "%02d:%02d:%02d", s / 3600, (s % 3600) / 60, s % 60)
    }
}

/// A simple label / value row.
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
