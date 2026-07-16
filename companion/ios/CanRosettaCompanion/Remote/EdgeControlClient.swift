import Foundation

/// Discovery mode requested from the AutoPi.
///
/// - `fast`: catalog scan (known OBD PIDs / UDS DIDs).
/// - `slow`: brute-force sweep. Slower but finds more.
enum EdgeMode: String, CaseIterable, Identifiable, Sendable {
    case fast
    case slow
    var id: String { rawValue }
    var label: String { rawValue.capitalized }
}

// MARK: - Wire models (match docs/control-protocol.md exactly)
//
// All decoding uses `keyDecodingStrategy = .convertFromSnakeCase` and all
// encoding uses `.convertToSnakeCase`, so idiomatic camelCase Swift names map to
// the protocol's snake_case JSON (`sw_version` <-> `swVersion`, `t_utc` <->
// `tUtc`, `edge_utc_offset_est_s` <-> `edgeUtcOffsetEstS`, etc.).

struct HealthResponse: Decodable, Sendable {
    let ok: Bool
    let swVersion: String?
}

struct TimeResponse: Decodable, Sendable {
    let tUtc: Double
}

struct EdgeDevice: Decodable, Sendable {
    let id: String?
    let swVersion: String?
}

struct EdgeStats: Decodable, Sendable {
    let elapsedS: Double?
    let frames: Int?
    let obdSamples: Int?
}

struct DiscoverySummary: Decodable, Sendable {
    let obdPids: Int?
    let udsDids: Int?
    let plainCanIds: Int?
}

struct StatusResponse: Decodable, Sendable {
    let state: String
    let sessionId: String?
    let outputDir: String?
    let device: EdgeDevice?
    let mode: String?
    let stats: EdgeStats?
    let discoverySummary: DiscoverySummary?
    let error: String?
}

struct SessionResponse: Decodable, Sendable {
    let sessionId: String
    let outputDir: String?
    let device: EdgeDevice?
}

/// Response for `discover` / `log/start` / `log/stop` / `run` — a state change,
/// optionally with a frame count on stop.
struct CommandResponse: Decodable, Sendable {
    let state: String?
    let frames: Int?
}

/// A single line off the `GET /api/ws` event stream. All fields are optional
/// because the shape depends on `event`; we decode a superset and read what the
/// event kind supplies.
struct EdgeEvent: Decodable, Sendable {
    let event: String
    let state: String?
    let phase: String?
    let supportedPids: Int?
    let summary: DiscoverySummary?
    let frames: Int?
    let obdSamples: Int?
    let elapsedS: Double?
    let message: String?
    let ts: Double?
}

// Request bodies.

private struct SessionRequest: Encodable {
    struct Vehicle: Encodable {
        let make: String?
        let model: String?
        let year: Int?
    }
    let sessionId: String?
    let vehicle: Vehicle?
    let edgeUtcOffsetEstS: Double?
    let clockSource: String?
}

private struct DiscoverRequest: Encodable {
    let mode: String
}

private struct RunRequest: Encodable {
    let mode: String
    let durationS: Double?
}

// MARK: - Errors

/// A user-presentable error from the control client.
struct EdgeError: LocalizedError, Sendable {
    let message: String
    let statusCode: Int?
    init(_ message: String, statusCode: Int? = nil) {
        self.message = message
        self.statusCode = statusCode
    }
    var errorDescription: String? { message }
}

// MARK: - Client

/// Stateless async client for the AutoPi control protocol
/// (`docs/control-protocol.md`). Value type holding only host + token, so it is
/// trivially `Sendable` and can be recreated cheaply whenever settings change.
struct EdgeControlClient: Sendable {

    let host: String
    let token: String

    private static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()

    private static let encoder: JSONEncoder = {
        let e = JSONEncoder()
        e.keyEncodingStrategy = .convertToSnakeCase
        return e
    }()

    private var trimmedHost: String {
        var h = host.trimmingCharacters(in: .whitespacesAndNewlines)
        while h.hasSuffix("/") { h.removeLast() }
        return h
    }

    // MARK: HTTP endpoints

    func health() async throws -> HealthResponse {
        try await perform(makeRequest("/api/health", method: "GET"))
    }

    func time() async throws -> TimeResponse {
        try await perform(makeRequest("/api/time", method: "GET"))
    }

    func status() async throws -> StatusResponse {
        try await perform(makeRequest("/api/status", method: "GET"))
    }

    func createSession(
        sessionId: String?,
        edgeUtcOffsetEstS: Double?,
        clockSource: String?
    ) async throws -> SessionResponse {
        let body = SessionRequest(
            sessionId: sessionId,
            vehicle: nil,
            edgeUtcOffsetEstS: edgeUtcOffsetEstS,
            clockSource: clockSource
        )
        return try await perform(makeRequest("/api/session", method: "POST",
                                             jsonBody: try Self.encoder.encode(body)))
    }

    func discover(mode: EdgeMode) async throws -> CommandResponse {
        let body = DiscoverRequest(mode: mode.rawValue)
        return try await perform(makeRequest("/api/discover", method: "POST",
                                             jsonBody: try Self.encoder.encode(body)))
    }

    func startLog() async throws -> CommandResponse {
        try await perform(makeRequest("/api/log/start", method: "POST"))
    }

    func stopLog() async throws -> CommandResponse {
        try await perform(makeRequest("/api/log/stop", method: "POST"))
    }

    func run(mode: EdgeMode, durationS: Double? = nil) async throws -> CommandResponse {
        let body = RunRequest(mode: mode.rawValue, durationS: durationS)
        return try await perform(makeRequest("/api/run", method: "POST",
                                             jsonBody: try Self.encoder.encode(body)))
    }

    // MARK: WebSocket event stream

    /// Live event stream from `GET /api/ws`. The task is created and resumed when
    /// iteration begins; cancelling the consuming task (or breaking the loop)
    /// tears the socket down via `onTermination`. On any socket failure the
    /// stream finishes with an error so the caller can fall back to polling.
    func events() -> AsyncThrowingStream<EdgeEvent, Error> {
        AsyncThrowingStream { continuation in
            let wsURL: URL
            do {
                wsURL = try makeWebSocketURL()
            } catch {
                continuation.finish(throwing: error)
                return
            }
            var request = URLRequest(url: wsURL)
            if !token.isEmpty {
                request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            }
            let task = URLSession.shared.webSocketTask(with: request)

            func receive() {
                task.receive { result in
                    switch result {
                    case .failure(let error):
                        continuation.finish(throwing: error)
                    case .success(let message):
                        switch message {
                        case .string(let text):
                            Self.emit(text, to: continuation)
                        case .data(let data):
                            if let text = String(data: data, encoding: .utf8) {
                                Self.emit(text, to: continuation)
                            }
                        @unknown default:
                            break
                        }
                        receive()
                    }
                }
            }

            task.resume()
            receive()
            continuation.onTermination = { _ in
                task.cancel(with: .goingAway, reason: nil)
            }
        }
    }

    private static func emit(
        _ text: String,
        to continuation: AsyncThrowingStream<EdgeEvent, Error>.Continuation
    ) {
        // A single frame may carry one or more newline-delimited JSON events.
        for line in text.split(separator: "\n") {
            guard let data = String(line).data(using: .utf8) else { continue }
            if let event = try? decoder.decode(EdgeEvent.self, from: data) {
                continuation.yield(event)
            }
        }
    }

    // MARK: Request plumbing

    private func makeURL(_ path: String) throws -> URL {
        guard let url = URL(string: trimmedHost + path) else {
            throw EdgeError("Invalid AutoPi host URL")
        }
        return url
    }

    private func makeWebSocketURL() throws -> URL {
        guard var comps = URLComponents(string: trimmedHost + "/api/ws") else {
            throw EdgeError("Invalid AutoPi host URL")
        }
        switch comps.scheme {
        case "http": comps.scheme = "ws"
        case "https": comps.scheme = "wss"
        default: break
        }
        if !token.isEmpty {
            comps.queryItems = [URLQueryItem(name: "token", value: token)]
        }
        guard let url = comps.url else {
            throw EdgeError("Invalid AutoPi WebSocket URL")
        }
        return url
    }

    private func makeRequest(_ path: String, method: String, jsonBody: Data? = nil) throws -> URLRequest {
        var request = URLRequest(url: try makeURL(path))
        request.httpMethod = method
        request.timeoutInterval = 15
        if !token.isEmpty {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        if let jsonBody {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = jsonBody
        }
        return request
    }

    private func perform<R: Decodable>(_ request: URLRequest) async throws -> R {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await URLSession.shared.data(for: request)
        } catch {
            throw EdgeError("Network error: \(error.localizedDescription)")
        }
        guard let http = response as? HTTPURLResponse else {
            throw EdgeError("Unexpected non-HTTP response")
        }
        guard (200..<300).contains(http.statusCode) else {
            throw EdgeError(Self.message(forStatus: http.statusCode), statusCode: http.statusCode)
        }
        do {
            return try Self.decoder.decode(R.self, from: data)
        } catch {
            throw EdgeError("Could not decode response: \(error.localizedDescription)")
        }
    }

    private static func message(forStatus code: Int) -> String {
        switch code {
        case 401: return "Unauthorized — check the bearer token"
        case 404: return "Not found (404)"
        case 409: return "AutoPi is busy — a job is already running (409)"
        default: return "AutoPi returned HTTP \(code)"
        }
    }
}
