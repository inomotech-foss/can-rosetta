import Foundation
import Combine
import os

/// Connection status to the AutoPi control server.
enum ConnectionState: Equatable {
    case idle
    case connecting
    case connected
    case failed(String)
}

/// Owns the phone side of the control channel: host/token settings (persisted in
/// `UserDefaults`), the measured edge/companion clock offset, the live edge
/// status, and the *coordinated* start/stop that drives the local
/// `RecordingController` in lock-step with the AutoPi.
///
/// The `session_id` is single-sourced from `RecordingController.sessionId` (the
/// phone mints it). The coordinated start sends that same id to the AutoPi via
/// `POST /api/session`, so both halves of the recording merge server-side.
@MainActor
final class EdgeConnection: ObservableObject {

    private enum Keys {
        static let host = "edge.host"
        static let token = "edge.token"
        static let mode = "edge.mode"
        static let wifiSSID = "edge.wifiSSID"
        static let wifiPSK = "edge.wifiPSK"
    }

    // MARK: - Persisted settings

    @Published var host: String { didSet { defaults.set(host, forKey: Keys.host) } }
    @Published var token: String { didSet { defaults.set(token, forKey: Keys.token) } }
    @Published var mode: EdgeMode { didSet { defaults.set(mode.rawValue, forKey: Keys.mode) } }
    /// AutoPi AP credentials from a v2 pairing payload; empty when the QR did
    /// not carry them (v1 / dev boxes). Joining lives in `WifiJoiner` — this
    /// class only persists what the QR provisioned.
    @Published var wifiSSID: String { didSet { defaults.set(wifiSSID, forKey: Keys.wifiSSID) } }
    @Published var wifiPSK: String { didSet { defaults.set(wifiPSK, forKey: Keys.wifiPSK) } }

    // MARK: - Live state

    @Published private(set) var connectionState: ConnectionState = .idle
    @Published private(set) var swVersion: String?
    @Published private(set) var edgeState: String = "unknown"
    @Published private(set) var frames: Int = 0
    @Published private(set) var obdSamples: Int = 0
    @Published private(set) var elapsed: Double = 0
    @Published private(set) var discoverySummary: DiscoverySummary?
    /// Measured `edge_utc - companion_utc` (seconds) from the best time sample.
    @Published private(set) var timeOffset: Double?
    /// Round-trip time of the best `GET /api/time` sample (seconds).
    @Published private(set) var timeRoundTrip: Double?
    @Published private(set) var wsConnected: Bool = false
    @Published private(set) var isBusy: Bool = false
    @Published var lastError: String?

    // MARK: - Internals

    private let defaults = UserDefaults.standard
    private let clock = Clock()
    private let logger = Logger(subsystem: AppInfo.subsystem, category: "remote")
    private var wsTask: Task<Void, Never>?
    private var pollTask: Task<Void, Never>?

    /// The clock source the companion reports (see `Clock` / manifest docs).
    private let clockSource = "gps"

    init() {
        host = defaults.string(forKey: Keys.host) ?? "http://192.168.4.1:8765"
        token = defaults.string(forKey: Keys.token) ?? ""
        mode = EdgeMode(rawValue: defaults.string(forKey: Keys.mode) ?? "") ?? .fast
        wifiSSID = defaults.string(forKey: Keys.wifiSSID) ?? ""
        wifiPSK = defaults.string(forKey: Keys.wifiPSK) ?? ""
    }

    var isConfigured: Bool {
        !host.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    /// Whether a v2 pairing payload provisioned the AutoPi's AP credentials.
    var hasWifiCredentials: Bool {
        !wifiSSID.isEmpty && !wifiPSK.isEmpty
    }

    /// A fresh, `Sendable` client snapshot of the current settings.
    private var client: EdgeControlClient {
        EdgeControlClient(host: host, token: token)
    }

    // MARK: - Pairing / health

    /// One-tap pairing: when the AutoPi's AP credentials are provisioned
    /// (QR v2), join its Wi-Fi first, then run the usual health check + time
    /// sync. Without credentials this is exactly the plain v1 pairing path.
    /// The `WifiJoiner` is owned by the view and injected here so this class
    /// stays transport-agnostic.
    func joinAndPair(joiner: WifiJoiner) async {
        if hasWifiCredentials {
            // The join outcome is informational only (the UI shows the joiner
            // state): the phone may already be on the AP, or reach the host
            // some other way (Simulator via the Mac, dev boxes on the LAN).
            // Always fall through to the health check — like the v1 scan flow
            // and the Android side — because reachability, not association,
            // is what pairing actually needs.
            await joiner.join(ssid: wifiSSID, psk: wifiPSK)
        }
        await checkHealth()
        if connectionState == .connected { await syncTime() }
    }

    func checkHealth() async {
        guard isConfigured else {
            connectionState = .failed("Enter the AutoPi host first")
            return
        }
        connectionState = .connecting
        lastError = nil
        do {
            let health = try await client.health()
            swVersion = health.swVersion
            connectionState = health.ok ? .connected : .failed("AutoPi reported not-ok")
            if health.ok { await refreshStatus() }
        } catch {
            connectionState = .failed(message(error))
        }
    }

    // MARK: - Time sync (Cristian's algorithm)

    /// Poll `GET /api/time` a few times and keep the sample with the smallest
    /// round-trip. Estimates `edge_utc_offset_est_s = edge_utc - companion_utc`
    /// using the app's monotonic-anchored `Clock` for the phone side.
    func syncTime(samples: Int = 5) async {
        guard isConfigured else { return }
        lastError = nil
        let cl = client
        var best: (offset: Double, rtt: Double)?
        for _ in 0..<max(1, samples) {
            let t0 = clock.nowUTC()
            do {
                let response = try await cl.time()
                let t1 = clock.nowUTC()
                let rtt = t1 - t0
                // Cristian: estimate the edge clock at t1, then subtract t1.
                let edgeAtT1 = response.tUtc + rtt / 2
                let offset = edgeAtT1 - t1
                if best == nil || rtt < best!.rtt {
                    best = (offset, rtt)
                }
            } catch {
                lastError = message(error)
                return
            }
        }
        if let best {
            timeOffset = best.offset
            timeRoundTrip = best.rtt
            logger.info("Time sync: offset=\(best.offset, format: .fixed(precision: 4))s rtt=\(best.rtt, format: .fixed(precision: 4))s")
        }
    }

    // MARK: - Investigation

    /// Point the AutoPi at the shared `session_id` (with the measured clock
    /// offset), then start a discovery run in the selected mode.
    func discover(sessionId: String) async {
        guard ensureConfigured() else { return }
        isBusy = true
        defer { isBusy = false }
        lastError = nil
        do {
            _ = try await client.createSession(sessionId: sessionId,
                                               edgeUtcOffsetEstS: timeOffset,
                                               clockSource: clockSource)
            _ = try await client.discover(mode: mode)
            connect()
        } catch {
            lastError = message(error)
        }
    }

    // MARK: - Coordinated recording

    /// Single action for "Start recording":
    /// 1. `POST /api/session` with the shared id + measured clock offset,
    /// 2. `POST /api/log/start` on the AutoPi,
    /// 3. start the phone's own `RecordingController` with the SAME `session_id`.
    ///
    /// The edge is started before the phone so that if the AutoPi rejects the
    /// request (e.g. busy) we never leave the phone recording alone.
    func startRecording(controller: RecordingController) async {
        guard ensureConfigured() else { return }
        guard !controller.isRecording else { return }
        isBusy = true
        defer { isBusy = false }
        lastError = nil
        let sessionId = controller.sessionId
        do {
            _ = try await client.createSession(sessionId: sessionId,
                                               edgeUtcOffsetEstS: timeOffset,
                                               clockSource: clockSource)
            _ = try await client.startLog()
        } catch {
            lastError = message(error)
            return
        }
        controller.start()   // phone side, same session_id
        connect()
        logger.info("Coordinated recording started for session \(sessionId, privacy: .public)")
    }

    /// Single action for "Stop": stop the phone recording and `POST
    /// /api/log/stop` on the AutoPi.
    func stopRecording(controller: RecordingController) async {
        isBusy = true
        defer { isBusy = false }
        if controller.isRecording {
            controller.stop()
        }
        do {
            _ = try await client.stopLog()
        } catch {
            lastError = message(error)
        }
        await refreshStatus()
    }

    // MARK: - Live status (WebSocket + polling fallback)

    /// Subscribe to the event stream, seeding from a status snapshot. If the
    /// socket drops or cannot connect, fall back to polling `GET /api/status`.
    func connect() {
        guard isConfigured else { return }
        disconnect()
        let cl = client
        Task { await self.refreshStatus() }
        wsTask = Task { [weak self] in
            do {
                for try await event in cl.events() {
                    guard let self else { return }
                    self.wsConnected = true
                    self.apply(event)
                }
            } catch {
                // fall through to polling
            }
            guard let self, !Task.isCancelled else { return }
            self.wsConnected = false
            self.startPolling()
        }
    }

    func disconnect() {
        wsTask?.cancel()
        wsTask = nil
        pollTask?.cancel()
        pollTask = nil
        wsConnected = false
    }

    func refreshStatus() async {
        guard isConfigured else { return }
        do {
            let status = try await client.status()
            applyStatus(status)
        } catch {
            // Non-fatal: keep last known state.
        }
    }

    private func startPolling() {
        pollTask?.cancel()
        let cl = client
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                do {
                    let status = try await cl.status()
                    guard let self else { return }
                    self.applyStatus(status)
                    self.wsConnected = false
                } catch {
                    // ignore transient errors while polling
                }
                try? await Task.sleep(nanoseconds: 2_000_000_000)
            }
        }
    }

    // MARK: - State application

    private func apply(_ event: EdgeEvent) {
        switch event.event {
        case "state":
            if let state = event.state { edgeState = state }
        case "discovery":
            break // progress phase — nothing to surface yet
        case "discovery_done":
            if let summary = event.summary { discoverySummary = summary }
        case "stats":
            if let frames = event.frames { self.frames = frames }
            if let obd = event.obdSamples { obdSamples = obd }
            if let el = event.elapsedS { elapsed = el }
        case "error":
            if let message = event.message { lastError = message }
        default:
            break
        }
    }

    private func applyStatus(_ status: StatusResponse) {
        edgeState = status.state
        if let stats = status.stats {
            frames = stats.frames ?? frames
            obdSamples = stats.obdSamples ?? obdSamples
            elapsed = stats.elapsedS ?? elapsed
        }
        if let summary = status.discoverySummary { discoverySummary = summary }
        if let version = status.device?.swVersion { swVersion = version }
        if let error = status.error { lastError = error }
        if connectionState != .connected { connectionState = .connected }
    }

    // MARK: - Helpers

    private func ensureConfigured() -> Bool {
        if isConfigured { return true }
        lastError = "Enter the AutoPi host first"
        return false
    }

    private func message(_ error: Error) -> String {
        (error as? EdgeError)?.message ?? error.localizedDescription
    }
}
