import Foundation
import Combine
import CoreLocation
import CoreMotion
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
///         ├── video.mp4           (only if "film dashboard" was on)
///         ├── video_index.jsonl   (only if "film dashboard" was on)
///         ├── photos/             (only if "capture stills" was on)
///         │   └── 000000.jpg ...
///         └── photos_index.jsonl  (only if "capture stills" was on)
@MainActor
final class RecordingController: ObservableObject {

    // MARK: - Published UI state

    /// Session id; agreed with the AutoPi (QR/manual). Editable while stopped.
    @Published var sessionId: String = SessionID.generate()
    @Published var filmDashboard: Bool = false
    /// Capture periodic full-resolution stills of the dashboard for OCR.
    @Published var capturePhotos: Bool = true
    /// Seconds between stills (see `PhotoCapture`).
    @Published var photoIntervalSeconds: Double = 0.5

    @Published private(set) var isRecording = false
    @Published private(set) var motionCount = 0
    @Published private(set) var locationCount = 0
    @Published private(set) var videoFrameCount = 0
    @Published private(set) var photoCount = 0
    /// Estimated live IMU sample rate (Hz).
    @Published private(set) var imuRateHz: Double = 0
    /// Horizontal accuracy of the latest GPS fix (m), or nil if no fix yet.
    @Published private(set) var gpsHorizontalAccuracy: Double?
    @Published private(set) var locationAuthorization: CLAuthorizationStatus = .notDetermined
    /// Seconds since recording started.
    @Published private(set) var elapsed: TimeInterval = 0
    /// Cumulative ground distance from GPS fixes (metres).
    @Published private(set) var distanceMeters: Double = 0
    /// Rolling standard deviation of accelerometer magnitude (g), measured by the
    /// pre-flight standby monitor — a proxy for how much the cradle rattles.
    @Published private(set) var mountVibrationRMS: Double = 0
    /// Whether the standby vibration monitor has enough data to judge the mount.
    @Published private(set) var hasMountData = false
    /// Sync markers (e.g. triple brake-flash) pinned into this session. Written
    /// into the manifest's `sync_markers` at stop, and re-persisted if a marker
    /// is pinned just after stopping.
    @Published private(set) var syncMarkers: [Manifest.SyncMarker] = []
    /// URL of the exportable zip archive produced at stop (for sharing).
    @Published private(set) var exportURL: URL?
    @Published private(set) var lastError: String?

    /// The standby monitor treats the cradle as steady below this g-RMS.
    let mountVibrationThreshold: Double = 0.08

    /// True when the phone is judged steady enough to record (or when we have no
    /// accelerometer data at all, e.g. the Simulator).
    var mountSteady: Bool { !hasMountData || mountVibrationRMS < mountVibrationThreshold }

    // MARK: - Internals

    private let logger = Logger(subsystem: AppInfo.subsystem, category: "recording")
    private let motionHz: Double = 100

    private var clock: Clock?
    private var motionSource: MotionSource?
    private var locationSource: LocationSource?
    private var videoRecorder: VideoRecorder?
    private var photoCapture: PhotoCapture?
    private var motionWriter: JSONLWriter?
    private var locationWriter: JSONLWriter?

    private var sessionDir: URL?
    private var startUTC: Double = 0

    private var uiTimer: Timer?
    private var lastRateSampleCount = 0
    private var lastRateSampleTime = Date()

    // Distance accumulation from GPS fixes.
    private var lastFixLocation: CLLocation?

    // Pre-flight standby vibration monitor (independent of the recording IMU).
    private let standbyMotion = CMMotionManager()
    private var accelMagnitudes: [Double] = []

    // Cached inputs so a post-stop sync marker can re-write the manifest/archive.
    private var finalizeStreams: [Manifest.Stream] = []
    private var finalizeEndUTC: Double = 0

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
            if filmDashboard || capturePhotos {
                _ = await VideoRecorder.requestCameraAuthorization()
            }
        }
    }

    // MARK: - Start / stop

    func start() {
        guard !isRecording else { return }
        lastError = nil
        exportURL = nil
        distanceMeters = 0
        lastFixLocation = nil
        syncMarkers = []
        // The recording IMU takes over from the standby vibration monitor.
        stopVibrationMonitor()

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
                Task { @MainActor in
                    guard let self else { return }
                    if record.hAcc >= 0 { self.gpsHorizontalAccuracy = record.hAcc }
                    self.accumulateDistance(record)
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

            // Periodic full-resolution stills (optional). Rides the video's
            // capture session when video is on (so video keeps recording),
            // otherwise stands up its own session. Setup never throws — a failure
            // just disables stills and leaves the rest of the recording intact.
            if capturePhotos {
                let photosDir = phoneDir.appendingPathComponent("photos", isDirectory: true)
                try? FileManager.default.createDirectory(at: photosDir, withIntermediateDirectories: true)
                let capture = PhotoCapture(
                    clock: clock,
                    photosDir: photosDir,
                    indexURL: phoneDir.appendingPathComponent("photos_index.jsonl"),
                    intervalSeconds: photoIntervalSeconds
                )
                if let recorder = self.videoRecorder, let device = recorder.captureDevice {
                    capture.startAttached(to: recorder.captureSession,
                                          sessionQueue: recorder.captureSessionQueue,
                                          device: device)
                } else {
                    capture.startStandalone()
                }
                self.photoCapture = capture
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
        // Stop firing new stills before we tear the capture session down.
        photoCapture?.stop()

        let motionRows = motionWriter?.rowCount ?? 0
        let locationRows = locationWriter?.rowCount ?? 0
        let hadVideo = videoRecorder != nil
        let photosSaved = photoCapture?.photoCount ?? 0
        let endUTC = clock?.nowUTC() ?? Date().timeIntervalSince1970

        Task {
            // Finalise the movie (async) before writing the manifest.
            if let recorder = videoRecorder {
                await recorder.stop()
            }
            if photosSaved > 0 {
                logger.info("Captured \(photosSaved) dashboard stills")
            }
            motionWriter?.close()
            locationWriter?.close()

            // Cache the stream set + end time so a sync marker pinned just after
            // stopping can re-write the manifest and re-export the archive.
            self.finalizeStreams = buildStreams(
                motionRows: motionRows,
                locationRows: locationRows,
                hadVideo: hadVideo,
                videoFrames: videoRecorder?.frameCount ?? 0,
                startUTC: startUTC,
                endUTC: endUTC
            )
            self.finalizeEndUTC = endUTC
            await rewriteManifestAndExport()

            // Reset transient owners. Keep `clock` and `sessionDir` so a
            // post-stop sync marker can stamp and re-persist.
            self.motionSource = nil
            self.videoRecorder = nil
            self.photoCapture = nil
            self.motionWriter = nil
            self.locationWriter = nil
        }
    }

    // MARK: - Sync markers

    /// Pin a sync marker (e.g. a triple brake-flash) into the current session.
    /// If recording, it lands in the manifest at stop; if pinned just after stop
    /// (the guided "sync marker" step), the manifest and archive are re-written.
    func addSyncMarker(kind: String, count: Int? = nil) {
        let t = clock?.nowUTC() ?? Date().timeIntervalSince1970
        syncMarkers.append(Manifest.SyncMarker(kind: kind, tUtc: t, count: count))
        logger.info("Pinned sync marker \(kind, privacy: .public) at \(t)")
        if !isRecording, sessionDir != nil {
            Task { await rewriteManifestAndExport() }
        }
    }

    /// Generate a fresh session id (only allowed while stopped).
    func newSessionID() {
        guard !isRecording else { return }
        sessionId = SessionID.generate()
    }

    // MARK: - Manifest & export

    private func buildStreams(
        motionRows: Int,
        locationRows: Int,
        hadVideo: Bool,
        videoFrames: Int,
        startUTC: Double,
        endUTC: Double
    ) -> [Manifest.Stream] {
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
        return streams
    }

    /// Write `manifest.json` (with the current sync markers) and re-export the
    /// zip archive. Safe to call again after stop when a marker is pinned.
    private func rewriteManifestAndExport() async {
        guard let dir = sessionDir else { return }
        // We report clock source "gps" (full-accuracy GNSS runs the whole
        // session). Honest caveat: iOS does not expose raw GPS time, so the
        // absolute offset is the system clock's; err_est_s reflects that we
        // cannot verify sub-100ms UTC accuracy on-device.
        let clockBlock = Manifest.Device.Clock(source: "gps", utcOffsetEstS: 0.0, errEstS: 0.1)
        let manifest = Manifest.make(
            sessionId: sessionId,
            createdUtc: startUTC,
            clock: clockBlock,
            streams: finalizeStreams,
            syncMarkers: syncMarkers
        )
        do {
            try manifest.write(to: dir.appendingPathComponent("manifest.json"))
            let archive = try exportArchive()
            self.exportURL = archive
            logger.info("Session finalised and archived at \(archive.lastPathComponent, privacy: .public)")
        } catch {
            logger.error("Failed to finalise session: \(error.localizedDescription)")
            self.lastError = error.localizedDescription
        }
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
        photoCount = photoCapture?.photoCount ?? 0
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

    // MARK: - Pre-flight monitors

    /// Start the live checks the pre-flight screen relies on: request location +
    /// camera, begin a standby GPS fix, and monitor accelerometer vibration to
    /// judge how firmly the phone is cradled. Idempotent.
    func startPreflight() {
        // Surface standby GPS accuracy live (recording start reassigns onRecord).
        standbyLocation.onRecord = { [weak self] record in
            guard record.hAcc >= 0 else { return }
            Task { @MainActor in self?.gpsHorizontalAccuracy = record.hAcc }
        }
        standbyLocation.requestAuthorization()
        standbyLocation.start()
        if filmDashboard || capturePhotos {
            Task { _ = await VideoRecorder.requestCameraAuthorization() }
        }
        startVibrationMonitor()
    }

    /// Stop the pre-flight vibration monitor (called when leaving pre-flight
    /// without recording). Location is left running; it is cheap and warms up
    /// the GPS fix for the drive.
    func stopPreflight() {
        stopVibrationMonitor()
    }

    private func startVibrationMonitor() {
        guard standbyMotion.isAccelerometerAvailable, !standbyMotion.isAccelerometerActive else { return }
        accelMagnitudes.removeAll()
        hasMountData = false
        mountVibrationRMS = 0
        standbyMotion.accelerometerUpdateInterval = 1.0 / 20.0
        let queue = OperationQueue()
        standbyMotion.startAccelerometerUpdates(to: queue) { [weak self] data, _ in
            guard let a = data?.acceleration else { return }
            let mag = (a.x * a.x + a.y * a.y + a.z * a.z).squareRoot()
            Task { @MainActor in self?.pushVibrationSample(mag) }
        }
    }

    private func stopVibrationMonitor() {
        if standbyMotion.isAccelerometerActive { standbyMotion.stopAccelerometerUpdates() }
        accelMagnitudes.removeAll()
        hasMountData = false
        mountVibrationRMS = 0
    }

    /// Rolling standard deviation of accelerometer magnitude over ~2 s.
    private func pushVibrationSample(_ mag: Double) {
        accelMagnitudes.append(mag)
        if accelMagnitudes.count > 40 {
            accelMagnitudes.removeFirst(accelMagnitudes.count - 40)
        }
        guard accelMagnitudes.count >= 10 else { return }
        let n = Double(accelMagnitudes.count)
        let mean = accelMagnitudes.reduce(0, +) / n
        let variance = accelMagnitudes.reduce(0) { $0 + ($1 - mean) * ($1 - mean) } / n
        mountVibrationRMS = variance.squareRoot()
        hasMountData = true
    }

    private func accumulateDistance(_ record: LocationRecord) {
        guard record.hAcc >= 0 else { return }
        let loc = CLLocation(latitude: record.lat, longitude: record.lon)
        if let last = lastFixLocation {
            let step = loc.distance(from: last)
            if step.isFinite { distanceMeters += step }
        }
        lastFixLocation = loc
    }

    /// Whether device motion (the recording IMU) is available on this device.
    var isMotionAvailable: Bool { standbyMotion.isDeviceMotionAvailable }

    /// Free disk available for important usage, in bytes (nil if unknown).
    static func freeDiskBytes() -> Int64? {
        let url = URL(fileURLWithPath: NSHomeDirectory())
        let values = try? url.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey])
        return values?.volumeAvailableCapacityForImportantUsage
    }

    private func cleanupAfterFailure() {
        motionSource?.stop()
        locationSource?.stop()
        photoCapture?.stop()
        motionWriter?.close()
        locationWriter?.close()
        motionSource = nil
        photoCapture = nil
        motionWriter = nil
        locationWriter = nil
        isRecording = false
        stopUITimer()
    }
}
