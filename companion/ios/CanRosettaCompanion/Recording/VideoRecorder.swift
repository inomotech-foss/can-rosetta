import Foundation
import AVFoundation
import os

/// One line of `phone/video_index.jsonl`:
/// `{ "frame": 0, "pts": 0.0, "t_utc": 1752624001.033 }`
///
/// `pts` is the presentation timestamp relative to the first written frame
/// (seconds); `tUtc` (-> `t_utc`) is that frame's absolute wall-clock time,
/// derived from the capture host-time clock via `Clock`.
struct VideoIndexRecord: Encodable {
    let frame: Int
    let pts: Double
    let tUtc: Double
}

/// Optional rear-camera capture of the dashboard to `phone/video.mp4` using
/// `AVAssetWriter`, plus a per-frame `phone/video_index.jsonl`.
///
/// Container timestamps are unreliable across players, so we write an explicit
/// index mapping each encoded frame to an honest `t_utc`. We take the *capture*
/// sample-buffer presentation timestamp (host-time clock domain), which is the
/// closest we can get to when the photons actually hit the sensor, and convert
/// it with the shared `Clock`.
final class VideoRecorder: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {

    private let clock: Clock
    private let logger = Logger(subsystem: AppInfo.subsystem, category: "video")

    private let session = AVCaptureSession()
    private let videoOutput = AVCaptureVideoDataOutput()
    private let sessionQueue = DispatchQueue(label: "\(AppInfo.subsystem).video.session")
    private let captureQueue = DispatchQueue(label: "\(AppInfo.subsystem).video.capture")

    private var writer: AVAssetWriter?
    private var writerInput: AVAssetWriterInput?
    private var indexWriter: JSONLWriter?

    private var startedSession = false
    private var firstPTS: CMTime = .invalid
    private var frameIndex = 0

    let videoURL: URL
    let indexURL: URL

    private(set) var frameCount = 0

    init(clock: Clock, videoURL: URL, indexURL: URL) {
        self.clock = clock
        self.videoURL = videoURL
        self.indexURL = indexURL
        super.init()
    }

    static var isCameraAuthorized: Bool {
        AVCaptureDevice.authorizationStatus(for: .video) == .authorized
    }

    static func requestCameraAuthorization() async -> Bool {
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized: return true
        case .notDetermined: return await AVCaptureDevice.requestAccess(for: .video)
        default: return false
        }
    }

    /// Configure the capture graph and asset writer, then start running.
    /// Throws if the camera or writer cannot be set up.
    func start() throws {
        try configureSession()
        try configureWriter()
        sessionQueue.async { [weak self] in
            self?.session.startRunning()
            self?.logger.info("Camera capture started")
        }
    }

    /// Stop capture, finish writing the movie and flush the index.
    func stop() async {
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            sessionQueue.async { [weak self] in
                guard let self else { continuation.resume(); return }
                self.session.stopRunning()
                guard let writer = self.writer, let input = self.writerInput else {
                    self.indexWriter?.close()
                    continuation.resume()
                    return
                }
                input.markAsFinished()
                writer.finishWriting {
                    self.indexWriter?.close()
                    self.logger.info("Video finalised: \(self.frameCount) frames, status \(writer.status.rawValue)")
                    continuation.resume()
                }
            }
        }
    }

    // MARK: - Setup

    private func configureSession() throws {
        session.beginConfiguration()
        session.sessionPreset = .high

        guard let device = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back) else {
            session.commitConfiguration()
            throw VideoError.noCamera
        }
        let input = try AVCaptureDeviceInput(device: device)
        guard session.canAddInput(input) else {
            session.commitConfiguration()
            throw VideoError.cannotAddInput
        }
        session.addInput(input)

        videoOutput.videoSettings = [
            kCVPixelBufferPixelFormatTypeKey as String: Int(kCVPixelFormatType_32BGRA)
        ]
        videoOutput.alwaysDiscardsLateVideoFrames = true
        videoOutput.setSampleBufferDelegate(self, queue: captureQueue)
        guard session.canAddOutput(videoOutput) else {
            session.commitConfiguration()
            throw VideoError.cannotAddOutput
        }
        session.addOutput(videoOutput)
        session.commitConfiguration()
    }

    private func configureWriter() throws {
        // Overwrite any stale file at the path.
        try? FileManager.default.removeItem(at: videoURL)

        let writer = try AVAssetWriter(outputURL: videoURL, fileType: .mp4)
        let settings: [String: Any] = [
            AVVideoCodecKey: AVVideoCodecType.h264,
            AVVideoWidthKey: 1920,
            AVVideoHeightKey: 1080,
            AVVideoCompressionPropertiesKey: [
                AVVideoAverageBitRateKey: 8_000_000,
                AVVideoProfileLevelKey: AVVideoProfileLevelH264HighAutoLevel
            ]
        ]
        let input = AVAssetWriterInput(mediaType: .video, outputSettings: settings)
        input.expectsMediaDataInRealTime = true
        guard writer.canAdd(input) else { throw VideoError.cannotAddInput }
        writer.add(input)

        self.writer = writer
        self.writerInput = input
        self.indexWriter = try JSONLWriter(url: indexURL)
    }

    // MARK: - AVCaptureVideoDataOutputSampleBufferDelegate

    func captureOutput(_ output: AVCaptureOutput,
                       didOutput sampleBuffer: CMSampleBuffer,
                       from connection: AVCaptureConnection) {
        guard let writer, let input = writerInput else { return }

        let pts = CMSampleBufferGetPresentationTimeStamp(sampleBuffer)

        if !startedSession {
            guard writer.startWriting() else {
                logger.error("AVAssetWriter failed to start: \(writer.error?.localizedDescription ?? "unknown")")
                return
            }
            writer.startSession(atSourceTime: pts)
            firstPTS = pts
            startedSession = true
        }

        guard input.isReadyForMoreMediaData else {
            logger.debug("Dropping frame: writer input not ready")
            return
        }
        guard input.append(sampleBuffer) else {
            logger.error("Failed to append sample buffer, status \(writer.status.rawValue)")
            return
        }

        let relativePTS = CMTimeSubtract(pts, firstPTS).seconds
        let record = VideoIndexRecord(
            frame: frameIndex,
            pts: relativePTS,
            tUtc: clock.utc(fromHostTime: pts)
        )
        indexWriter?.append(record)
        frameIndex += 1
        frameCount = frameIndex
    }

    enum VideoError: LocalizedError {
        case noCamera, cannotAddInput, cannotAddOutput
        var errorDescription: String? {
            switch self {
            case .noCamera: return "No rear camera available"
            case .cannotAddInput: return "Cannot add camera input"
            case .cannotAddOutput: return "Cannot add video output"
            }
        }
    }
}
