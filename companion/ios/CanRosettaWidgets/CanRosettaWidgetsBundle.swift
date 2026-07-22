import WidgetKit
import SwiftUI

/// The companion's WidgetKit extension. iOS 26 surfaces an app's widgets and
/// Live Activities on the **CarPlay Dashboard without any CarPlay entitlement**
/// — that no-approval path is why this extension exists: it puts recording
/// status and a Stop button on the head unit while the phone records in the
/// cradle (see `RecordingLiveActivity` / `RecordingStatusWidget`).
@main
struct CanRosettaWidgetsBundle: WidgetBundle {
    var body: some Widget {
        RecordingStatusWidget()
        RecordingLiveActivity()
    }
}
