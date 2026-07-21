import SwiftUI

/// 01 — Pair AutoPi. A live QR viewfinder configures the `EdgeConnection` from a
/// JSON payload and runs the Cristian time-sync; a manual host/token fallback
/// reuses the same logic. A v2 payload also carries the AutoPi's AP
/// credentials — the view then joins the Wi-Fi programmatically (`WifiJoiner`)
/// before the handshake, so the user never leaves the app for Settings.
/// "Confirm — arm both recorders" advances to pre-flight.
struct PairView: View {
    @EnvironmentObject private var controller: RecordingController
    @EnvironmentObject private var connection: EdgeConnection
    @EnvironmentObject private var flow: DriveFlowModel

    // View-owned (not app-level environment): the joiner is only relevant to
    // pairing, and `EdgeConnection.joinAndPair` takes it as a parameter.
    @StateObject private var joiner = WifiJoiner()

    @State private var qrRead = false
    @State private var scannerUnavailable = false
    @State private var showAdvanced = false

    private let phrase = ["towel", "dolphin", "babel"]

    private var handshakeComplete: Bool { connection.connectionState == .connected }

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    header
                    viewfinder
                    timeSyncLine
                    manualPairing
                    detailsCard
                    advancedLink
                }
                .padding(.horizontal, 20)
                .padding(.top, 8)
                .padding(.bottom, 12)
            }
            VStack(spacing: 10) {
                PrimaryButton(title: "Confirm — arm both recorders") {
                    flow.confirmPairing(controller: controller, connection: connection)
                }
                Button("Record without AutoPi") {
                    flow.recordStandalone(controller: controller)
                }
                .font(.subheadline).foregroundStyle(Theme.textMuted)
            }
            .padding(.horizontal, 20)
            .padding(.bottom, 8)
        }
        .onAppear {
            // A headless AutoPi's AP gateway is the sensible default — user just
            // adds the token.
            if connection.host.trimmingCharacters(in: .whitespaces).isEmpty {
                connection.host = "http://192.168.4.1:8765"
            }
        }
    }

    // MARK: Header

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            SectionLabel(text: "Step 1 of 5")
            Text("Pair AutoPi")
                .font(.system(size: 28, weight: .bold))
                .foregroundStyle(Theme.text)
            Text("Scan the AutoPi's QR to exchange host, token and session id — or enter them by hand below. A headless AutoPi has no screen, so manual entry is a first-class option.")
                .font(.subheadline)
                .foregroundStyle(Theme.textSecondary)
        }
    }

    // MARK: Viewfinder

    private var viewfinder: some View {
        ZStack {
            LinearGradient(colors: [Color(hex: 0x0B1220), Color(hex: 0x03060D)],
                           startPoint: .topLeading, endPoint: .bottomTrailing)

            if !scannerUnavailable {
                QRScannerView(isActive: !qrRead, onCode: handleCode,
                              onUnavailable: { _ in DispatchQueue.main.async { scannerUnavailable = true } })
            } else {
                VStack(spacing: 8) {
                    Image(systemName: "qrcode.viewfinder").font(.system(size: 40))
                        .foregroundStyle(Theme.textMuted)
                    Text("Camera unavailable — use manual pairing below")
                        .font(.caption).foregroundStyle(Theme.textMuted)
                        .multilineTextAlignment(.center)
                }
                .padding()
            }

            if !qrRead { CornerBrackets() }

            if qrRead {
                Color.black.opacity(0.5)
                VStack(spacing: 10) {
                    ZStack {
                        Circle().fill(Theme.greenFill).frame(width: 56, height: 56)
                        Image(systemName: "checkmark").font(.system(size: 24, weight: .bold))
                            .foregroundStyle(Theme.green)
                    }
                    Text("QR read — handshake complete")
                        .font(.system(.subheadline, weight: .semibold))
                        .foregroundStyle(Theme.green)
                }
            }
        }
        .frame(height: 230)
        .clipShape(RoundedRectangle(cornerRadius: Theme.cardRadius, style: .continuous))
    }

    private var timeSyncLine: some View {
        HStack(spacing: 8) {
            Image(systemName: "clock.arrow.2.circlepath")
                .foregroundStyle(handshakeComplete ? Theme.green : Theme.textMuted)
            if let offset = connection.timeOffset, let rtt = connection.timeRoundTrip {
                Text(String(format: "offset %+.0f ms · rtt %.0f ms", offset * 1000, rtt * 1000))
                    .font(.mono(.caption)).foregroundStyle(Theme.green)
            } else {
                Text("time-sync pending")
                    .font(.mono(.caption)).foregroundStyle(Theme.textMuted)
            }
        }
    }

    // MARK: Manual pairing (first-class, headless-friendly)

    private var manualPairing: some View {
        FlowCard {
            SectionLabel(text: "Host + token")
            Spacer().frame(height: 6)
            Text("Headless AutoPi? The installer prints the host + token (and a QR you can scan from your SSH terminal) — enter them here or scan above.")
                .font(.caption).foregroundStyle(Theme.textMuted)
            Spacer().frame(height: 12)
            FlowField(placeholder: "http://192.168.4.1:8765", text: $connection.host, secure: false)
            Spacer().frame(height: 10)
            FlowField(placeholder: "Control token", text: $connection.token, secure: true)
            Spacer().frame(height: 14)
            PrimaryButton(title: connection.connectionState == .connecting ? "Pairing…" : "Pair",
                          enabled: connection.isConfigured && !connection.isBusy,
                          background: Color.white.opacity(0.10)) {
                Task { await pairManually() }
            }
            if connection.hasWifiCredentials {
                // Credentials were provisioned by a v2 QR — offer the
                // programmatic join (+ pair) without re-scanning.
                Spacer().frame(height: 10)
                PrimaryButton(title: joiner.state == .joining ? "Joining Wi-Fi…" : "Join AutoPi Wi-Fi",
                              enabled: joiner.state != .joining && !connection.isBusy,
                              background: Color.white.opacity(0.10)) {
                    Task { await connection.joinAndPair(joiner: joiner) }
                }
                if case .failed(let reason) = joiner.state {
                    Spacer().frame(height: 8)
                    Text(reason).font(.caption).foregroundStyle(Theme.redLight)
                } else if case .applied = joiner.state {
                    // Neutral: apply was accepted but the SSID couldn't be
                    // confirmed — the handshake line decides success.
                    Spacer().frame(height: 8)
                    Text("join requested; couldn't confirm from here")
                        .font(.mono(.caption)).foregroundStyle(Theme.textMuted)
                }
            }
            if handshakeComplete {
                Spacer().frame(height: 8)
                Text("Handshake complete.").font(.caption).foregroundStyle(Theme.green)
            } else if case .failed(let reason) = connection.connectionState {
                Spacer().frame(height: 8)
                Text(reason).font(.caption).foregroundStyle(Theme.redLight)
            }
        }
    }

    // MARK: Details

    private var detailsCard: some View {
        FlowCard(padding: 6) {
            VStack(spacing: 0) {
                InfoRow(label: "Session", value: shortSession).padding(.horizontal, 12)
                RowSeparator(leadingInset: 12)
                InfoRow(label: "Wi-Fi", value: wifiStatus.text, valueColor: wifiStatus.color)
                    .padding(.horizontal, 12)
                RowSeparator(leadingInset: 12)
                InfoRow(label: "Control token",
                        value: handshakeComplete ? "verified" : "unverified",
                        valueColor: handshakeComplete ? Theme.green : Theme.textMuted)
                    .padding(.horizontal, 12)
                RowSeparator(leadingInset: 12)
                HStack(alignment: .center) {
                    Text("Pairing phrase").font(.system(.subheadline)).foregroundStyle(Theme.textSecondary)
                    Spacer(minLength: 12)
                    HStack(spacing: 6) { ForEach(phrase, id: \.self) { Chip(text: $0) } }
                }
                .padding(.vertical, 11).padding(.horizontal, 12)
            }
        }
    }

    private var advancedLink: some View {
        Button { showAdvanced = true } label: {
            Label("Advanced control", systemImage: "slider.horizontal.3")
                .font(.caption).foregroundStyle(Theme.textMuted)
        }
        .sheet(isPresented: $showAdvanced) {
            NavigationStack { RemoteControlView() }.preferredColorScheme(.dark)
        }
    }

    // MARK: Actions

    private var shortSession: String {
        String(controller.sessionId.prefix(13)) + (controller.sessionId.count > 13 ? "…" : "")
    }

    /// The details-card Wi-Fi row: SSID + live join state when the AP
    /// credentials were provisioned (QR v2), otherwise a muted
    /// "not provisioned" — the v1 look.
    private var wifiStatus: (text: String, color: Color) {
        guard connection.hasWifiCredentials else { return ("not provisioned", Theme.textMuted) }
        switch joiner.state {
        case .idle: return (connection.wifiSSID, Theme.textSecondary)
        case .joining: return ("joining \(connection.wifiSSID)…", Theme.textSecondary)
        case .joined(let ssid): return (ssid, Theme.green)
        // Join requested but unconfirmed — neutral, not an error; the
        // handshake result below is what proves reachability.
        case .applied(let ssid): return ("\(ssid) — join requested", Theme.textSecondary)
        case .failed: return ("\(connection.wifiSSID) — join failed", Theme.redLight)
        case .unavailable(let reason): return (reason, Theme.textMuted)
        }
    }

    private func handleCode(_ text: String) {
        guard let payload = PairingPayload.decode(text) else { return }
        connection.host = payload.host
        connection.token = payload.token
        // v2 payloads provision the AP credentials. A payload without `wifi`
        // clears any stale ones: the QR is the source of truth for this
        // pairing, and a leftover SSID would join the wrong AutoPi's AP.
        connection.wifiSSID = payload.wifi?.ssid ?? ""
        connection.wifiPSK = payload.wifi?.psk ?? ""
        if let sid = payload.sessionId, !sid.isEmpty, !controller.isRecording {
            controller.sessionId = sid
        }
        qrRead = true
        // Joins the AP first when provisioned; degrades to plain
        // health-check + time-sync (the v1 flow) when it is not.
        Task { await connection.joinAndPair(joiner: joiner) }
    }

    private func pairManually() async {
        await connection.checkHealth()
        if connection.connectionState == .connected { await connection.syncTime() }
    }
}

/// A dark rounded text field for the manual pairing inputs.
struct FlowField: View {
    let placeholder: String
    @Binding var text: String
    var secure: Bool
    var body: some View {
        Group {
            if secure {
                SecureField(placeholder, text: $text)
            } else {
                TextField(placeholder, text: $text)
                    .keyboardType(.URL)
            }
        }
        .textInputAutocapitalization(.never)
        .autocorrectionDisabled()
        .font(.mono(.subheadline))
        .foregroundStyle(Theme.text)
        .padding(.horizontal, 14).padding(.vertical, 12)
        .background(Color.white.opacity(0.06), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
    }
}

#Preview {
    PairView()
        .environmentObject(RecordingController())
        .environmentObject(EdgeConnection())
        .environmentObject(DriveFlowModel())
        .preferredColorScheme(.dark)
}
