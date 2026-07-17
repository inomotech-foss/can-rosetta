import Foundation
import AVFoundation
import CoreMedia
import ImageIO
import os

/// One line of `phone/photos_index.jsonl`:
/// `{ "t_utc": 1752624001.5, "path": "phone/photos/000001.jpg", "w": 4032, "h": 3024 }`
///
/// `w`/`h` are the still's pixel dimensions (optional per the schema). Property
/// names are camelCase; `JSONLWriter` encodes with `.convertToSnakeCase`, so
/// `tUtc` lands on the wire as `t_utc`.
struct PhotoIndexRecord: Encodable {
    let tUtc: Double
    let path: String
    let w: Int?
    let h: Int?
}

/// Periodic full-resolution still capture of the dashboard to `phone/photos/`,
/// indexed by `phone/photos_index.jsonl`.
///
/// ## Why stills *and* video?
///
/// The video (`VideoRecorder`) is temporally dense but low-resolution and
/// HEVC/H.264-compressed — great for a turn-signal blink or a needle sweep, poor
/// for OCR of small dashboard digits. So we *also* fire a full-resolution JPEG on
/// a timer. The server routes numeric/gear OCR to the nearest still and
/// telltales/needles to the video.
///
/// ## Coexistence with video
///
/// An `AVCapturePhotoOutput` is attached to the **same** `AVCaptureSession` the
/// video uses, so the video keeps recording uninterrupted (`startAttached`). When
/// video is disabled, this class stands up its own capture session with the rear
/// camera (`startStandalone`). Only one `AVCaptureSession` ever owns the camera.
///
/// ## Timestamps
///
/// Each `AVCapturePhoto` carries a `timestamp` (`CMTime`) in the capture host-time
/// clock domain — the same domain as the video sample-buffer PTS. We map it
/// through the shared `Clock` to the identical `t_utc` domain as motion and video,
/// so a still, a video frame and an IMU sample taken at the same instant share a
/// comparable `t_utc`.
///
/// ## Robustness
///
/// Setup never throws: if the session cannot accept a photo output (e.g. a
/// multicam limit) or the camera is unavailable, stills are disabled with a log
/// and recording continues.
final class PhotoCapture: NSObject, AVCapturePhotoCaptureDelegate {

    private let clock: Clock
    private let photosDir: URL
    private let indexURL: URL
    /// Time between stills, seconds. Configurable (default set by the caller).
    let intervalSeconds: Double
    private let logger = Logger(subsystem: AppInfo.subsystem, category: "photo")

    private let photoOutput = AVCapturePhotoOutput()

    // Capture graph — either shared with the video recorder or owned by us.
    private var session: AVCaptureSession!
    private var sessionQueue: DispatchQueue!
    private var device: AVCaptureDevice?
    private var ownsSession = false

    // Firing timer runs on `sessionQueue`.
    private var timer: DispatchSourceTimer?
    private var running = false
    private var maxDimensions: CMVideoDimensions?

    // File writes + the running counter live on their own serial queue so the
    // photo-delivery callback never blocks on disk I/O.
    private let ioQueue = DispatchQueue(label: "\(AppInfo.subsystem).photo.io")
    private var savedCount = 0
    private var indexWriter: JSONLWriter?

    // Guard against unbounded outstanding captures if the sensor lags the timer.
    private let inFlightLock = NSLock()
    private var inFlight = 0
    private let maxInFlight = 4

    init(clock: Clock, photosDir: URL, indexURL: URL, intervalSeconds: Double) {
        self.clock = clock
        self.photosDir = photosDir
        self.indexURL = indexURL
        self.intervalSeconds = max(0.05, intervalSeconds)
        super.init()
    }

    // MARK: - Start

    /// Attach a photo output to an already-running capture session (the video
    /// recorder's). The video keeps recording; the graph reconfiguration happens
    /// on the shared session queue. Never throws — disables stills on failure.
    func startAttached(to session: AVCaptureSession, sessionQueue: DispatchQueue, device: AVCaptureDevice) {
        self.session = session
        self.sessionQueue = sessionQueue
        self.device = device
        self.ownsSession = false
        guard prepareWriter() else { return }
        configureAndRun(buildOwnSession: false)
    }

    /// Stand up a dedicated capture session with the rear camera (used when video
    /// is off). Never throws — disables stills on failure.
    func startStandalone() {
        self.session = AVCaptureSession()
        self.sessionQueue = DispatchQueue(label: "\(AppInfo.subsystem).photo.session")
        self.ownsSession = true
        guard prepareWriter() else { return }
        configureAndRun(buildOwnSession: true)
    }

    private func prepareWriter() -> Bool {
        do {
            try FileManager.default.createDirectory(at: photosDir, withIntermediateDirectories: true)
            indexWriter = try JSONLWriter(url: indexURL)
            return true
        } catch {
            logger.error("Cannot create photos index, stills disabled: \(error.localizedDescription)")
            return false
        }
    }

    private func configureAndRun(buildOwnSession: Bool) {
        sessionQueue.async { [self] in
            if buildOwnSession {
                // Pass 1: input + preset, committed so the active format settles
                // before we read its supported photo dimensions.
                session.beginConfiguration()
                session.sessionPreset = .photo
                guard let dev = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back) else {
                    session.commitConfiguration()
                    disable("no rear camera available for stills")
                    return
                }
                do {
                    let input = try AVCaptureDeviceInput(device: dev)
                    guard session.canAddInput(input) else {
                        session.commitConfiguration()
                        disable("cannot add camera input for stills")
                        return
                    }
                    session.addInput(input)
                } catch {
                    session.commitConfiguration()
                    disable("camera input error: \(error.localizedDescription)")
                    return
                }
                device = dev
                session.commitConfiguration()
            }

            guard let device else {
                disable("no capture device for stills")
                return
            }

            // Pass 2: add the photo output and configure high-resolution capture.
            session.beginConfiguration()
            guard session.canAddOutput(photoOutput) else {
                session.commitConfiguration()
                disable("session cannot add a photo output (multicam/device limit)")
                return
            }
            session.addOutput(photoOutput)
            photoOutput.maxPhotoQualityPrioritization = .quality
            // Largest still the active format supports == full sensor resolution.
            if let best = device.activeFormat.supportedMaxPhotoDimensions
                .max(by: { Int($0.width) * Int($0.height) < Int($1.width) * Int($1.height) }) {
                photoOutput.maxPhotoDimensions = best
                maxDimensions = best
            }
            session.commitConfiguration()

            if buildOwnSession {
                session.startRunning()
            }
            running = true
            startTimer()
            let dims = maxDimensions.map { "\($0.width)x\($0.height)" } ?? "device default"
            let msg = "Still capture started: interval \(intervalSeconds)s, max \(dims)"
            logger.info("\(msg, privacy: .public)")
        }
    }

    private func disable(_ reason: String) {
        logger.error("Still capture disabled: \(reason, privacy: .public)")
        running = false
        ioQueue.async { [self] in
            indexWriter?.close()
            indexWriter = nil
        }
    }

    // MARK: - Firing

    private func startTimer() {
        let t = DispatchSource.makeTimerSource(queue: sessionQueue)
        t.schedule(deadline: .now() + intervalSeconds, repeating: intervalSeconds)
        t.setEventHandler { [weak self] in self?.fire() }
        t.resume()
        timer = t
    }

    /// Runs on `sessionQueue`.
    private func fire() {
        guard running else { return }
        inFlightLock.lock()
        if inFlight >= maxInFlight {
            let n = inFlight
            inFlightLock.unlock()
            logger.debug("Skipping still: \(n) captures already in flight")
            return
        }
        inFlight += 1
        inFlightLock.unlock()
        photoOutput.capturePhoto(with: makeSettings(), delegate: self)
    }

    /// Fresh settings are required per capture. Runs on `sessionQueue`.
    private func makeSettings() -> AVCapturePhotoSettings {
        let settings: AVCapturePhotoSettings
        if photoOutput.availablePhotoCodecTypes.contains(.jpeg) {
            settings = AVCapturePhotoSettings(format: [AVVideoCodecKey: AVVideoCodecType.jpeg])
        } else {
            settings = AVCapturePhotoSettings()
        }
        settings.photoQualityPrioritization = .quality
        if let maxDimensions {
            settings.maxPhotoDimensions = maxDimensions
        }
        return settings
    }

    // MARK: - Stop

    /// Stop firing new captures, tear down our own session (if we own it) and
    /// flush the index. Returns immediately; teardown is asynchronous.
    func stop() {
        timer?.cancel()
        timer = nil
        sessionQueue?.async { [self] in
            running = false
            if ownsSession { session?.stopRunning() }
        }
        // Close after any already-enqueued index appends have run (FIFO on ioQueue).
        ioQueue.async { [self] in
            indexWriter?.close()
            indexWriter = nil
        }
    }

    /// Stills saved so far (thread-safe), for the live UI / logging.
    var photoCount: Int {
        ioQueue.sync { savedCount }
    }

    // MARK: - AVCapturePhotoCaptureDelegate

    func photoOutput(_ output: AVCapturePhotoOutput,
                     didFinishProcessingPhoto photo: AVCapturePhoto,
                     error: Error?) {
        if let error {
            logger.error("Photo processing failed: \(error.localizedDescription)")
            return
        }
        guard let data = photo.fileDataRepresentation() else {
            logger.error("Photo produced no JPEG data")
            return
        }
        // Capture time in the host-time clock domain -> same t_utc domain as video.
        let tUtc = clock.utc(fromHostTime: photo.timestamp)
        let dims = Self.jpegPixelSize(data)

        ioQueue.async { [self] in
            let index = savedCount
            let name = String(format: "%06d.jpg", index)
            let fileURL = photosDir.appendingPathComponent(name)
            do {
                try data.write(to: fileURL, options: .atomic)
            } catch {
                logger.error("Failed to write still \(name, privacy: .public): \(error.localizedDescription)")
                return
            }
            let record = PhotoIndexRecord(
                tUtc: tUtc,
                path: "phone/photos/\(name)",
                w: dims?.0,
                h: dims?.1
            )
            indexWriter?.append(record)
            savedCount = index + 1
        }
    }

    func photoOutput(_ output: AVCapturePhotoOutput,
                     didFinishCaptureFor resolvedSettings: AVCaptureResolvedSettings,
                     error: Error?) {
        inFlightLock.lock()
        if inFlight > 0 { inFlight -= 1 }
        inFlightLock.unlock()
    }

    // MARK: - Helpers

    /// Read the encoded pixel dimensions from JPEG data via ImageIO (robust and
    /// exact, without decoding the whole image).
    private static func jpegPixelSize(_ data: Data) -> (Int, Int)? {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil),
              let props = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              let w = props[kCGImagePropertyPixelWidth] as? Int,
              let h = props[kCGImagePropertyPixelHeight] as? Int else {
            return nil
        }
        return (w, h)
    }
}
