import Foundation
import AppIntents

/// The tiny seam between the widget-facing App Intents and the app's
/// `RecordingController`.
///
/// This file is compiled into **both** targets because the widget needs the
/// intent *types* to build `Button(intent:)`, while the intent *actions* only
/// make sense in the app process (that is where the sensors, writers and the
/// Live Activity live). The handlers below are registered by
/// `RecordingController` in the app; in the widget extension process they stay
/// `nil` and `perform()` is a harmless no-op.
///
/// Process model, honestly: the intents conform to `LiveActivityIntent`, which
/// the system runs **in the app's process** (launching it in the background if
/// needed). Stop and marker work whenever the app process is alive — which it
/// is for the whole recording, because background *location* keeps it running
/// in the cradle. If the app was killed there is no recording to stop, so a
/// cold background launch finding no handler is the correct no-op. Starting a
/// recording is deliberately NOT an intent: start needs the pre-flight flow
/// (permissions, mount check, pairing), so the idle widget instead deep-links
/// into the app via `widgetURL`.
@MainActor
final class RecordingWidgetActions {
    static let shared = RecordingWidgetActions()
    private init() {}

    /// Set by `RecordingController.init` (app process only).
    var stopRecording: (() -> Void)?
    /// Pins a sync marker into the running session (see `addSyncMarker`).
    var pinSyncMarker: (() -> Void)?
}

/// Stop the running recording — the red button on the widget / Live Activity
/// (and thus on the iOS 26 CarPlay Dashboard).
@available(iOS 17.0, *)
struct StopRecordingIntent: LiveActivityIntent {
    static var title: LocalizedStringResource = "Stop recording"
    static var description = IntentDescription(
        "Stops the current CAN-Rosetta drive recording and finalises the session archive.")
    /// Buttons construct this directly; keep it out of Shortcuts/Spotlight —
    /// stopping a drive from a search result would be a surprising data loss.
    static var isDiscoverable: Bool = false
    /// Must act without foregrounding the app: on the CarPlay Dashboard there
    /// is no phone UI to bring up.
    static var openAppWhenRun: Bool = false

    @MainActor
    func perform() async throws -> some IntentResult {
        RecordingWidgetActions.shared.stopRecording?()
        return .result()
    }
}

/// Pin a sync marker into the running session — same semantic as the guided
/// "flash the brakes 3×" step (`SyncMarkerView`): the driver performs the
/// triple brake-flash and taps this, stamping `t_utc` for server-side
/// alignment of phone and CAN clocks. Exposed on the Live Activity so it can
/// be done from the head unit mid-drive, not only after stopping.
@available(iOS 17.0, *)
struct PinSyncMarkerIntent: LiveActivityIntent {
    static var title: LocalizedStringResource = "Pin sync marker"
    static var description = IntentDescription(
        "Marks the moment of a triple brake-flash so the phone and CAN recordings can be aligned.")
    static var isDiscoverable: Bool = false
    static var openAppWhenRun: Bool = false

    @MainActor
    func perform() async throws -> some IntentResult {
        RecordingWidgetActions.shared.pinSyncMarker?()
        return .result()
    }
}
