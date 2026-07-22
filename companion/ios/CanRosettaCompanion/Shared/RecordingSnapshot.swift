import Foundation

/// The app-group bridge between the app and the widget extension.
///
/// This file is compiled into **both** targets (see `project.yml`): the app
/// writes, the widget's timeline provider reads. Only the app process observes
/// the sensors, so everything the widget shows travels through this snapshot.
enum RecordingWidgetBridge {
    /// App Group shared by app and widget extension. An "unmanaged capability":
    /// declared via the checked-in `.entitlements` files, registered by
    /// automatic signing (there is no Xcode capabilities UI in an
    /// XcodeGen-generated project).
    static let appGroupID = "group.com.inomotech.canrosetta.companion"

    /// `WidgetKit` kind of the status widget, used by the app to reload its
    /// timelines when the recording state changes.
    static let statusWidgetKind = "RecordingStatusWidget"

    /// Key under which the JSON-encoded `RecordingSnapshot` lives in the shared
    /// `UserDefaults`.
    static let snapshotKey = "recording_snapshot"

    /// Shared defaults, or `nil` when the app group container is unavailable
    /// (e.g. a build signed without the entitlement). Callers degrade to
    /// "no data" — a widget must never take the recording down.
    static var sharedDefaults: UserDefaults? {
        UserDefaults(suiteName: appGroupID)
    }
}

/// Compact, JSON-encoded state of the recorder, published by
/// `RecordingController` into the shared `UserDefaults` (throttled to ~1 Hz)
/// and rendered by the status widget.
///
/// This is app-internal plumbing, **not** part of the session data format —
/// keys stay camelCase and nothing here is written into a session part.
struct RecordingSnapshot: Codable {
    var sessionId: String
    var isRecording: Bool
    /// Unix seconds when recording started; `nil` while idle. The widget
    /// renders a live-ticking timer from this (`Text(_:style: .timer)`), so it
    /// keeps counting between timeline reloads.
    var startedAtUTC: Double?
    var motionCount: Int
    var locationCount: Int
    /// Horizontal accuracy of the latest GPS fix (m); `nil` before a fix.
    var gpsAccuracyM: Double?
    /// Unix seconds of the write — lets the widget flag a stale snapshot
    /// (e.g. the app was killed mid-recording and stopped publishing).
    var updatedAtUTC: Double

    // MARK: - Shared-defaults round trip

    /// Store into the app group (no-op when the group is unavailable).
    func store() {
        guard let defaults = RecordingWidgetBridge.sharedDefaults,
              let data = try? JSONEncoder().encode(self) else { return }
        defaults.set(data, forKey: RecordingWidgetBridge.snapshotKey)
    }

    /// Load the last published snapshot, or `nil` if none was ever written
    /// (fresh install) or the group is unavailable.
    static func load() -> RecordingSnapshot? {
        guard let defaults = RecordingWidgetBridge.sharedDefaults,
              let data = defaults.data(forKey: RecordingWidgetBridge.snapshotKey) else { return nil }
        return try? JSONDecoder().decode(RecordingSnapshot.self, from: data)
    }
}
