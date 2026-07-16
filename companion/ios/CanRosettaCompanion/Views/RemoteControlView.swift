import SwiftUI

/// Pairing + remote-control UI for the AutoPi: connect, sync clocks, pick a
/// discovery mode, discover, and drive a coordinated (phone + edge) recording.
///
/// The `session_id` shown here is `RecordingController.sessionId` — the single
/// shared id the phone mints and sends to the AutoPi.
struct RemoteControlView: View {
    @EnvironmentObject private var connection: EdgeConnection
    @EnvironmentObject private var controller: RecordingController

    var body: some View {
        Form {
            pairingSection
            timeSyncSection
            investigationSection
            recordingSection
            statusSection
            if let error = connection.lastError {
                Section {
                    Text(error)
                        .foregroundStyle(.red)
                        .font(.footnote)
                }
            }
        }
        .navigationTitle("Remote AutoPi")
        .navigationBarTitleDisplayMode(.inline)
    }

    // MARK: - Pairing

    private var pairingSection: some View {
        Section {
            TextField("http://192.168.4.1:8765", text: $connection.host)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .keyboardType(.URL)
                .font(.system(.body, design: .monospaced))
                .disabled(controller.isRecording)
            SecureField("Bearer token", text: $connection.token)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .disabled(controller.isRecording)
            Button {
                Task { await connection.checkHealth() }
            } label: {
                Label("Connect", systemImage: "link")
            }
            .disabled(connection.isBusy)
            connectionStatusRow
        } header: {
            Text("AutoPi")
        } footer: {
            Text("Connect the phone to the AutoPi's Wi-Fi access point, then enter its control host and pre-shared token.")
        }
    }

    private var connectionStatusRow: some View {
        HStack {
            Text("Status")
            Spacer()
            switch connection.connectionState {
            case .idle:
                Text("Not connected").foregroundStyle(.secondary)
            case .connecting:
                Text("Connecting…").foregroundStyle(.secondary)
            case .connected:
                Text(connection.swVersion.map { "Connected · \($0)" } ?? "Connected")
                    .foregroundStyle(.green)
            case .failed(let reason):
                Text(reason).foregroundStyle(.red).multilineTextAlignment(.trailing)
            }
        }
        .font(.footnote)
    }

    // MARK: - Time sync

    private var timeSyncSection: some View {
        Section {
            Button {
                Task { await connection.syncTime() }
            } label: {
                Label("Sync clocks", systemImage: "clock.arrow.2.circlepath")
            }
            .disabled(connection.isBusy || connection.connectionState != .connected)
            if let offset = connection.timeOffset {
                StatusRow(label: "Edge − phone offset",
                          value: String(format: "%+.1f ms", offset * 1000))
            }
            if let rtt = connection.timeRoundTrip {
                StatusRow(label: "Round-trip", value: String(format: "%.1f ms", rtt * 1000))
            }
        } header: {
            Text("Time sync")
        } footer: {
            Text("Cristian's algorithm over a few samples; the best (smallest round-trip) offset is sent to the AutoPi so both clocks share a prior.")
        }
    }

    // MARK: - Investigation

    private var investigationSection: some View {
        Section {
            Picker("Mode", selection: $connection.mode) {
                ForEach(EdgeMode.allCases) { mode in
                    Text(mode.label).tag(mode)
                }
            }
            .pickerStyle(.segmented)
            .disabled(controller.isRecording)
            Button {
                Task { await connection.discover(sessionId: controller.sessionId) }
            } label: {
                Label("Discover", systemImage: "magnifyingglass")
            }
            .disabled(connection.isBusy || connection.connectionState != .connected)
            if let summary = connection.discoverySummary {
                StatusRow(label: "OBD PIDs", value: "\(summary.obdPids ?? 0)")
                StatusRow(label: "UDS DIDs", value: "\(summary.udsDids ?? 0)")
                StatusRow(label: "Plain CAN IDs", value: "\(summary.plainCanIds ?? 0)")
            }
        } header: {
            Text("Investigation")
        } footer: {
            Text("Fast = catalog scan; Slow = brute-force sweep.")
        }
    }

    // MARK: - Coordinated recording

    private var recordingSection: some View {
        Section {
            HStack {
                Text("Session ID")
                Spacer()
                Text(controller.sessionId)
                    .foregroundStyle(.secondary)
                    .font(.system(.footnote, design: .monospaced))
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            Button {
                Task {
                    if controller.isRecording {
                        await connection.stopRecording(controller: controller)
                    } else {
                        await connection.startRecording(controller: controller)
                    }
                }
            } label: {
                Label(controller.isRecording ? "Stop recording" : "Start recording",
                      systemImage: controller.isRecording ? "stop.circle" : "record.circle")
                    .frame(maxWidth: .infinity)
            }
            .tint(controller.isRecording ? .red : .accentColor)
            .disabled(connection.isBusy || !connection.isConfigured)
        } header: {
            Text("Coordinated recording")
        } footer: {
            Text("Starts the phone recording and the AutoPi log together, using the same session id so the server can merge both parts.")
        }
    }

    // MARK: - Live status

    private var statusSection: some View {
        Section("Edge status") {
            StatusRow(label: "State", value: connection.edgeState)
            StatusRow(label: "Frames", value: "\(connection.frames)")
            StatusRow(label: "OBD samples", value: "\(connection.obdSamples)")
            StatusRow(label: "Elapsed", value: String(format: "%.0f s", connection.elapsed))
            StatusRow(label: "Live feed", value: connection.wsConnected ? "WebSocket" : "Polling")
        }
    }
}

#Preview {
    NavigationStack {
        RemoteControlView()
            .environmentObject(RecordingController())
            .environmentObject(EdgeConnection())
    }
}
