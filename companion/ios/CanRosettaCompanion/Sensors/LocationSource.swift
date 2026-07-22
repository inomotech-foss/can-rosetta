import Foundation
import CoreLocation
import os

/// One line of `phone/location.jsonl`. Field names/units follow
/// `docs/data-format.md` and `schemas/location.record.schema.json`:
///
/// - `lat`, `lon`   degrees                                — required
/// - `alt`          altitude, m
/// - `speed`        m/s over ground, `-1` if unknown
/// - `course`       degrees from true north, `-1` if unknown
/// - `hAcc`/`vAcc`  horizontal/vertical accuracy, m
/// - `producedByAccessory`  optional; true when the fix came from an external
///   accessory rather than the phone's own GNSS (see below)
///
/// `tUtc` encodes to `t_utc`, `hAcc`/`vAcc` to `h_acc`/`v_acc`,
/// `producedByAccessory` to `produced_by_accessory` (omitted when nil — the
/// synthesized `Encodable` uses `encodeIfPresent` for optionals).
struct LocationRecord: Encodable {
    let tUtc: Double
    let lat: Double
    let lon: Double
    let alt: Double
    let speed: Double
    let course: Double
    let hAcc: Double
    let vAcc: Double
    /// Wireless CarPlay head units transparently feed vehicle GNSS (typically
    /// wheel-speed-aided dead reckoning) into CoreLocation, so a docked
    /// iPhone's "GPS" may actually be the car's — not an independent phone
    /// reference. The pipeline needs this flag to tell fused fixes from
    /// phone-only GPS. Written only when CoreLocation reports source
    /// information (iOS 15+ API; our floor is 16, but the field itself can be
    /// absent), so `nil` here means "provenance unknown", not "phone GPS".
    let producedByAccessory: Bool?
}

/// Wraps `CLLocationManager` at best accuracy for driving, mapping each fix to
/// a `LocationRecord`. Handles authorization. Emits raw fixes only — no
/// smoothing or interpolation.
final class LocationSource: NSObject, CLLocationManagerDelegate {

    private let manager = CLLocationManager()
    private let logger = Logger(subsystem: AppInfo.subsystem, category: "location")

    /// Called for every fix, on the main queue (CoreLocation's delegate queue).
    var onRecord: ((LocationRecord) -> Void)?
    /// Called when authorization status changes.
    var onAuthorizationChange: ((CLAuthorizationStatus) -> Void)?

    /// Latest reported horizontal accuracy (m), or `nil` if no valid fix yet.
    /// Read from the main thread for the live UI.
    private(set) var lastHorizontalAccuracy: Double?

    /// Whether we want background updates (requires "Always" auth + the
    /// `location` background mode). Recording while the screen is off / app
    /// backgrounded is common in a cradle, so default to true.
    var wantsBackgroundUpdates = true

    override init() {
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyBestForNavigation
        manager.distanceFilter = kCLDistanceFilterNone
        manager.activityType = .automotiveNavigation
        manager.pausesLocationUpdatesAutomatically = false
    }

    var authorizationStatus: CLAuthorizationStatus { manager.authorizationStatus }

    /// Ask for the strongest authorization we can. Call before `start()`.
    func requestAuthorization() {
        switch manager.authorizationStatus {
        case .notDetermined:
            // "When in use" first; iOS will offer the upgrade to "Always".
            manager.requestWhenInUseAuthorization()
        case .authorizedWhenInUse:
            if wantsBackgroundUpdates { manager.requestAlwaysAuthorization() }
        default:
            break
        }
    }

    func start() {
        let status = manager.authorizationStatus
        guard status == .authorizedWhenInUse || status == .authorizedAlways else {
            logger.error("Cannot start location: not authorized (status \(status.rawValue))")
            return
        }
        // Background updates are only legal with "Always" + the background mode.
        if wantsBackgroundUpdates, status == .authorizedAlways {
            manager.allowsBackgroundLocationUpdates = true
            manager.showsBackgroundLocationIndicator = true
        }
        manager.startUpdatingLocation()
        logger.info("Started location updates")
    }

    func stop() {
        manager.stopUpdatingLocation()
        manager.allowsBackgroundLocationUpdates = false
        logger.info("Stopped location updates")
    }

    // MARK: - CLLocationManagerDelegate

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        logger.info("Location authorization changed: \(manager.authorizationStatus.rawValue)")
        onAuthorizationChange?(manager.authorizationStatus)
    }

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        for loc in locations {
            // Use the fix's own timestamp: CoreLocation stamps each fix at
            // acquisition time on the same wall clock, and it is the most
            // accurate time we have for a GPS sample.
            let record = LocationRecord(
                tUtc: loc.timestamp.timeIntervalSince1970,
                lat: loc.coordinate.latitude,
                lon: loc.coordinate.longitude,
                alt: loc.altitude,
                speed: loc.speed >= 0 ? loc.speed : -1,
                course: loc.course >= 0 ? loc.course : -1,
                hAcc: loc.horizontalAccuracy,
                vAcc: loc.verticalAccuracy,
                // GPS provenance: `sourceInformation` is nil when CoreLocation
                // has nothing to say (e.g. Simulator); only then do we omit the
                // field, keeping "unknown" distinct from "phone GPS" (false).
                producedByAccessory: loc.sourceInformation?.isProducedByAccessory
            )
            if loc.horizontalAccuracy >= 0 {
                lastHorizontalAccuracy = loc.horizontalAccuracy
            }
            onRecord?(record)
        }
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        logger.error("Location error: \(error.localizedDescription)")
    }
}
