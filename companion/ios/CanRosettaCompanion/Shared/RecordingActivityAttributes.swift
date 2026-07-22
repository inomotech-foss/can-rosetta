import Foundation
import ActivityKit

/// ActivityKit attributes for the "drive is recording" Live Activity.
///
/// Compiled into **both** targets: the app starts/updates/ends the activity
/// (`RecordingController`), the widget extension renders it
/// (`RecordingLiveActivity`). ActivityKit matches the two by this type, so it
/// must be byte-identical on both sides — hence one shared file, not two copies.
///
/// Why a Live Activity at all: iOS 26 surfaces Live Activities (and widgets) on
/// the **CarPlay Dashboard without any CarPlay entitlement** — the
/// zero-approval path to a driver-visible recording status + Stop button on the
/// head unit while the phone records in the cradle.
///
/// `@available`: ActivityKit ships in iOS 16.1, but the modern
/// `ActivityContent` start/update/end API is 16.2 — the app target floors at
/// 16.0, so everything Live-Activity is gated at 16.2. The widget target floors
/// at 17.0 and needs no guards.
@available(iOS 16.2, *)
struct RecordingActivityAttributes: ActivityAttributes {
    /// The dynamic part, pushed by `RecordingController` (throttled: on
    /// significant change or ~5 s — ActivityKit itself rate-limits updates).
    struct ContentState: Codable, Hashable {
        var isRecording: Bool
        /// Seconds since recording started at the time of the update. The view
        /// re-anchors a live-ticking timer from this, so the display keeps
        /// counting between (sparse) updates.
        var elapsed: TimeInterval
        var motionCount: Int
        var locationCount: Int
        /// Horizontal accuracy of the latest GPS fix (m); `nil` before a fix.
        var gpsAccuracy: Double?
    }

    /// Fixed for the lifetime of the activity — one activity per session.
    var sessionId: String
}
