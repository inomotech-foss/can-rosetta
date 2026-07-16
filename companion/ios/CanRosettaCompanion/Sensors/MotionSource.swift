import Foundation
import CoreMotion
import os

/// One line of `phone/motion.jsonl`. Field names/units follow
/// `docs/data-format.md` and `schemas/motion.record.schema.json`:
///
/// - `acc`     user acceleration, g (gravity removed)   — required
/// - `gravity` gravity vector, g
/// - `rot`     rotation rate, rad/s (x, y, z)            — required
/// - `att`     attitude [roll, pitch, yaw], rad
/// - `mag`     calibrated magnetic field, µT (nullable)
///
/// `tUtc` encodes to `t_utc`.
struct MotionRecord: Encodable {
    let tUtc: Double
    let acc: [Double]
    let gravity: [Double]?
    let rot: [Double]
    let att: [Double]
    let mag: [Double]?
}

/// Wraps `CMMotionManager` device-motion updates (a fused IMU stream:
/// accelerometer + gyroscope + magnetometer + attitude) at ~100 Hz and maps
/// each `CMDeviceMotion` to a `MotionRecord` stamped with honest `t_utc`.
///
/// It does not resample or filter — every sample is emitted at its true
/// acquisition time, per the data-format rule "producers never resample".
final class MotionSource {

    private let manager = CMMotionManager()
    private let queue: OperationQueue
    private let clock: Clock
    private let logger = Logger(subsystem: AppInfo.subsystem, category: "motion")

    /// Called for every sample, on the CoreMotion operation queue.
    var onRecord: ((MotionRecord) -> Void)?

    /// Requested sample rate in Hz.
    let hz: Double

    init(clock: Clock, hz: Double = 100) {
        self.clock = clock
        self.hz = hz
        let q = OperationQueue()
        q.name = "\(AppInfo.subsystem).motion"
        q.maxConcurrentOperationCount = 1
        q.qualityOfService = .userInitiated
        self.queue = q
    }

    var isAvailable: Bool { manager.isDeviceMotionAvailable }

    func start() {
        guard manager.isDeviceMotionAvailable else {
            logger.error("Device motion not available on this device")
            return
        }

        manager.deviceMotionUpdateInterval = 1.0 / hz
        manager.showsDeviceMovementDisplay = true

        // Prefer true-north-referenced yaw (needs magnetometer + location,
        // which we run anyway). Fall back gracefully.
        let frame = bestAttitudeReferenceFrame()
        logger.info("Starting device motion at \(self.hz, format: .fixed(precision: 0)) Hz, frame \(frame.rawValue)")

        manager.startDeviceMotionUpdates(using: frame, to: queue) { [weak self] motion, error in
            guard let self else { return }
            if let error {
                self.logger.error("Motion update error: \(error.localizedDescription)")
                return
            }
            guard let m = motion else { return }

            let mag: [Double]?
            switch m.magneticField.accuracy {
            case .uncalibrated:
                mag = nil // don't emit garbage — schema allows null/absent
            default:
                let f = m.magneticField.field
                mag = [f.x, f.y, f.z]
            }

            let record = MotionRecord(
                tUtc: self.clock.utc(fromUptime: m.timestamp),
                acc: [m.userAcceleration.x, m.userAcceleration.y, m.userAcceleration.z],
                gravity: [m.gravity.x, m.gravity.y, m.gravity.z],
                rot: [m.rotationRate.x, m.rotationRate.y, m.rotationRate.z],
                att: [m.attitude.roll, m.attitude.pitch, m.attitude.yaw],
                mag: mag
            )
            self.onRecord?(record)
        }
    }

    func stop() {
        manager.stopDeviceMotionUpdates()
        logger.info("Stopped device motion")
    }

    private func bestAttitudeReferenceFrame() -> CMAttitudeReferenceFrame {
        let available = CMMotionManager.availableAttitudeReferenceFrames()
        if available.contains(.xTrueNorthZVertical) { return .xTrueNorthZVertical }
        if available.contains(.xMagneticNorthZVertical) { return .xMagneticNorthZVertical }
        if available.contains(.xArbitraryCorrectedZVertical) { return .xArbitraryCorrectedZVertical }
        return .xArbitraryZVertical
    }
}
