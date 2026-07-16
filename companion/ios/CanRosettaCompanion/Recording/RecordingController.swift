import Foundation
import Combine
import CoreLocation
import os

/// Orchestrates one recording session: owns the sensor sources, the writers and
/// the (optional) video recorder; creates the session directory; and writes the
/// manifest at stop. Publishes live counters for the UI.
///
/// Layout produced (relative to the app's Documents directory):
///
///     sessions/session-<id>/
///     ├── manifest.json
///     └── phone/
///         ├── motion.jsonl
///         ├── location.jsonl
///         ├── video.mp4          (only if "film dashboard" was on)
///         └── video_index.jsonl  (only if "film dashboard" was on)
@MainActor
final class RecordingController: ObservableObject {

    // MARK: - Published UI state

    /// Session id; agreed with the AutoPi (QR/manual). Editable while stopped.
    @Published var sessionId: String = SessionID.generate()
    @Published var filmDashboard: Bool = false

    @Published private(set) var isRecording = false
    @Published private(set) var motionCount = 0
    @Published private(set) var locationCount = 0
    @Published private(set) var videoFrameCount = 0
    /// Estimated live IMU sample rate (Hz).
    @Published private(set) var imuRateHz: Double = 0
    /// Horizontal accuracy of the latest GPS fix (m), or nil if no fix yet.
    @Published private(set) var gpsHorizontalAccuracy: Double?
    @Published private(set) var locationAuthorization: CLAuthorizationStatus = .notDetermined
    /// Seconds since recording started.
    @Published private(set) var elapsed: TimeInterval = 0
    /// URL of the exportable zip archive produced at stop (for sharing).
    @Published private(set) var exportURL: URL?
    @Published private(set) var lastError: String?

    // MARK: - Internals

    private let logger = Logger(subsystem: AppInfo.subsystem, category: "recording")
    private let motionHz: Double = 100

    private var clock: Clock?
    private var motionSource: MotionSource?
    private var locationSource: LocationSource?
    private var videoRecorder: VideoRecorder?
    private var motionWriter: JSONLWriter?
    private var locationWriter: JSONLWriter?

    private var sessionDir: URL?
    private var startUTC: Double = 0

    private var uiTimer: Timer?
    private var lastRateSampleCount = 0
    private var lastRateSampleTime = Date()

    /// A persistent location source so we can request authorization and show
    /// GPS status before the user hits record.
    private lazy var standbyLocation: LocationSource = {
        let src = LocationSource()
        src.onAuthorizationChange = { [weak self] status in
            Task { @MainActor in self?.locationAuthorization = status }
        }
        return src
    }()

    init() {
        locationAuthorization = standbyLocation.authorizationStatus
    }

    /// Ask for the permissions we need up front (motion is prompted lazily by
    /// the OS on first use; location we request explicitly).
    func requestPermissions() {
        standbyLocation.requestAuthorization()
        Task {
            if filmDashboard {
                _ = await VideoRecorder.requestCameraAuthorization()
            }
        }
    }

    // MARK: - Start / stop

    func start() {
        guard !isRecording else { return }
        lastError = nil
        exportURL = nil

        do {
            let clock = Clock()
            self.clock = clock
            self.startUTC = clock.nowUTC()

            let dir = try makeSessionDirectory(id: sessionId)
            self.sessionDir = dir
            let phoneDir = dir.appendingPathComponent("phone", isDirectory: true)

            // Writers
            let motionWriter = try JSONLWriter(url: phoneDir.appendingPathComponent("motion.jsonl"))
            let locationWriter = try JSONLWriter(url: phoneDir.appendingPathComponent("location.jsonl"))
            self.motionWriter = motionWriter
            self.locationWriter = locationWriter

            // Motion
            let motion = MotionSource(clock: clock, hz: motionHz)
            motion.onRecord = { motionWriter.append($0) }
            self.motionSource = motion
            motion.start()

            // Location — reuse the standby manager (already authorized/observed).
            let location = standbyLocation
            location.onRecord = { [weak self] record in
                locationWriter.append(record)
                if record.hAcc >= 0 {
                    Task { @MainActor in self?.gpsHorizontalAccuracy = record.hAcc }
                }
            }
            self.locationSource = location
            location.requestAuthorization()
            location.start()

            // Video (optional)
            if filmDashboard {
                let recorder = VideoRecorder(
                    clock: clock,
                    videoURL: phoneDir.appendingPathComponent("video.mp4"),
                    indexURL: phoneDir.appendingPathComponent("video_index.jsonl")
                )
                do {
                    try recorder.start()
                    self.videoRecorder = recorder
                } catch {
                    logger.error("Video start failed, continuing without video: \(error.localizedDescription)")
                    lastError = "Video unavailable: \(error.localizedDescription)"
                }
            }

            isRecording = true
            startUITimer()
            logger.info("Recording started for session \(self.sessionId, privacy: .public)")
        } catch {
            logger.error("Failed to start recording: \(error.localizedDescription)")
            lastError = error.localizedDescription
            cleanupAfterFailure()
        }
    }

    func stop() {
        guard isRecording else { return }
        isRecording = false
        stopUITimer()

        motionSource?.stop()
        locationSource?.stop()
        locationSource?.onRecord = nil

        let motionRows = motionWriter?.rowCount ?? 0
        let locationRows = locationWriter?.rowCount ?? 0
        let hadVideo = videoRecorder != nil
        let endUTC = clock?.nowUTC() ?? Date().timeIntervalSince1970

        Task {
            // Finalise the movie (async) before writing the manifest.
            if let recorder = videoRecorder {
                await recorder.stop()
            }
            motionWriter?.close()
            locationWriter?.close()

            do {
                try writeManifest(
                    motionRows: motionRows,
                    locationRows: locationRows,
                    hadVideo: hadVideo,
                    videoFrames: videoRecorder?.frameCount ?? 0,
                    startUTC: startUTC,
                    endUTC: endUTC
                )
                let archive = try exportArchive()
                self.exportURL = archive
                logger.info("Session finalised and archived at \(archive.lastPathComponent, privacy: .public)")
            } catch {
                logger.error("Failed to finalise session: \(error.localizedDescription)")
                self.lastError = error.localizedDescription
            }

            // Reset transient owners.
            self.motionSource = nil
            self.videoRecorder = nil
            self.motionWriter = nil
            self.locationWriter = nil
        }
    }

    /// Generate a fresh session id (only allowed while stopped).
    func newSessionID() {
        guard !isRecording else { return }
        sessionId = SessionID.generate()
    }

    // MARK: - Manifest & export

    private func writeManifest(
        motionRows: Int,
        locationRows: Int,
        hadVideo: Bool,
        videoFrames: Int,
        startUTC: Double,
        endUTC: Double
    ) throws {
        guard let dir = sessionDir else { throw CocoaError(.fileNoSuchFile) }

        var streams: [Manifest.Stream] = [
            Manifest.Stream(path: "phone/motion.jsonl", kind: "motion", rows: motionRows,
                            index: nil, tStartUtc: startUTC, tEndUtc: endUTC),
            Manifest.Stream(path: "phone/location.jsonl", kind: "location", rows: locationRows,
                            index: nil, tStartUtc: startUTC, tEndUtc: endUTC)
        ]
        if hadVideo, videoFrames > 0 {
            streams.append(Manifest.Stream(
                path: "phone/video.mp4", kind: "video", rows: videoFrames,
                index: "phone/video_index.jsonl", tStartUtc: startUTC, tEndUtc: endUTC))
        }

        // We report clock source "gps" (full-accuracy GNSS runs the whole
        // session). Honest caveat: iOS does not expose raw GPS time, so the
        // absolute offset is the system clock's; err_est_s reflects that we
        // cannot verify sub-100ms UTC accuracy on-device.
        let clockBlock = Manifest.Device.Clock(
            source: "gps",
            utcOffsetEstS: 0.0,
            errEstS: 0.1
        )

        let manifest = Manifest.make(
            sessionId: sessionId,
            createdUtc: startUTC,
            clock: clockBlock,
            streams: streams
        )
        try manifest.write(to: dir.appendingPathComponent("manifest.json"))
    }

    /// Zip the session directory into a shareable archive using the Foundation
    /// file coordinator (`.forUploading` produces a zip — no third-party dep).
    private func exportArchive() throws -> URL {
        guard let dir = sessionDir else { throw CocoaError(.fileNoSuchFile) }

        var coordinatorError: NSError?
        var resultURL: URL?
        var copyError: Error?

        let coordinator = NSFileCoordinator()
        coordinator.coordinate(readingItemAt: dir, options: [.forUploading], error: &coordinatorError) { zippedURL in
            // `zippedURL` is a temporary zip that Foundation deletes when the
            // block returns; copy it somewhere stable to share.
            let dest = FileManager.default.temporaryDirectory
                .appendingPathComponent("session-\(sessionId).zip")
            try? FileManager.default.removeItem(at: dest)
            do {
                try FileManager.default.copyItem(at: zippedURL, to: dest)
                resultURL = dest
            } catch {
                copyError = error
            }
        }
        if let coordinatorError { throw coordinatorError }
        if let copyError { throw copyError }
        guard let resultURL else { throw CocoaError(.fileWriteUnknown) }
        return resultURL
    }

    private func makeSessionDirectory(id: String) throws -> URL {
        let docs = try FileManager.default.url(for: .documentDirectory, in: .userDomainMask,
                                               appropriateFor: nil, create: true)
        let dir = docs
            .appendingPathComponent("sessions", isDirectory: true)
            .appendingPathComponent("session-\(id)", isDirectory: true)
        let phone = dir.appendingPathComponent("phone", isDirectory: true)
        try FileManager.default.createDirectory(at: phone, withIntermediateDirectories: true)
        return dir
    }

    // MARK: - Live UI timer

    private func startUITimer() {
        lastRateSampleCount = 0
        lastRateSampleTime = Date()
        let timer = Timer(timeInterval: 0.25, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.tick() }
        }
        RunLoop.main.add(timer, forMode: .common)
        uiTimer = timer
    }

    private func stopUITimer() {
        uiTimer?.invalidate()
        uiTimer = nil
    }

    private func tick() {
        elapsed = (clock?.nowUTC() ?? Date().timeIntervalSince1970) - startUTC

        let mCount = motionWriter?.rowCount ?? 0
        motionCount = mCount
        locationCount = locationWriter?.rowCount ?? 0
        videoFrameCount = videoRecorder?.frameCount ?? 0
        gpsHorizontalAccuracy = locationSource?.lastHorizontalAccuracy

        let now = Date()
        let dt = now.timeIntervalSince(lastRateSampleTime)
        if dt >= 0.5 {
            let delta = mCount - lastRateSampleCount
            imuRateHz = Double(delta) / dt
            lastRateSampleCount = mCount
            lastRateSampleTime = now
        }
    }

    private func cleanupAfterFailure() {
        motionSource?.stop()
        locationSource?.stop()
        motionWriter?.close()
        locationWriter?.close()
        motionSource = nil
        motionWriter = nil
        locationWriter = nil
        isRecording = false
        stopUITimer()
    }
}
