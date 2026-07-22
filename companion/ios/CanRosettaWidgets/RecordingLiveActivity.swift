import ActivityKit
import WidgetKit
import SwiftUI

/// Live Activity of a running recording: lock screen / StandBy banner, Dynamic
/// Island, and — on iOS 26 — the **CarPlay Dashboard**, which shows Live
/// Activities from a docked iPhone with no CarPlay entitlement. That last
/// surface is the design driver: the driver sees REC + elapsed + sample counts
/// on the head unit and can stop the drive or pin a sync marker without
/// touching the phone.
///
/// CarPlay (and the Apple Watch Smart Stack) render the **`.small` activity
/// family**, not the lock-screen banner — so the Live Activity only reaches the
/// Dashboard when the extension declares `supplementalActivityFamilies([.small])`
/// *and* the content view adapts to `\.activityFamily == .small`. Both are done
/// below; without them iOS 26 shows nothing on CarPlay for this activity.
///
/// `RecordingController` owns the lifecycle (request → throttled updates →
/// end); this file is pure presentation over
/// `RecordingActivityAttributes.ContentState`.
struct RecordingLiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: RecordingActivityAttributes.self) { context in
            // Lock screen / StandBy / CarPlay Dashboard banner. The banner reads
            // `\.activityFamily` and collapses to a glanceable row when the
            // system asks for `.small` (CarPlay Dashboard / Watch Smart Stack).
            RecordingActivityBanner(state: context.state)
                .activityBackgroundTint(Color(red: 0.04, green: 0.05, blue: 0.09))
                .activitySystemActionForegroundColor(.white)
        } dynamicIsland: { context in
            DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    RecordingStatePill(isRecording: context.state.isRecording)
                }
                DynamicIslandExpandedRegion(.trailing) {
                    elapsedText(context.state)
                        .font(.system(.title3, design: .monospaced).weight(.semibold))
                }
                DynamicIslandExpandedRegion(.bottom) {
                    VStack(spacing: 8) {
                        Text(sampleCountsLine(context.state))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        if context.state.isRecording {
                            RecordingActivityButtons()
                        }
                    }
                }
            } compactLeading: {
                RecordingDot(isRecording: context.state.isRecording)
            } compactTrailing: {
                elapsedText(context.state)
                    .font(.system(.caption2, design: .monospaced))
            } minimal: {
                RecordingDot(isRecording: context.state.isRecording)
            }
        }
        // Opt the activity into the compact family CarPlay (and the Watch Smart
        // Stack) present. Without this the Dashboard has no view to render and
        // the activity simply never appears in the car.
        .supplementalActivityFamilies([.small])
    }
}

// MARK: - Shared pieces (banner + island)

/// Live-ticking elapsed time. The state only carries `elapsed` (seconds at the
/// last update), so re-anchor a `.timer` Text at "now − elapsed": it ticks
/// locally between the controller's sparse (~5 s) updates and merely re-anchors
/// — sub-second jitter at worst — when one arrives. After stop, freeze at the
/// final duration.
private func elapsedText(_ state: RecordingActivityAttributes.ContentState) -> Text {
    guard state.isRecording else {
        return Text(Duration.seconds(state.elapsed).formatted(.time(pattern: .hourMinuteSecond)))
    }
    return Text(Date(timeIntervalSinceNow: -state.elapsed), style: .timer)
}

private func sampleCountsLine(_ state: RecordingActivityAttributes.ContentState) -> String {
    var line = "\(state.motionCount.formattedCompact) IMU · \(state.locationCount.formattedCompact) GPS"
    if let acc = state.gpsAccuracy {
        line += String(format: " ±%.0f m", acc)
    }
    return line
}

private struct RecordingDot: View {
    let isRecording: Bool
    var body: some View {
        Circle()
            .fill(isRecording ? Color.red : Color.gray)
            .frame(width: 10, height: 10)
    }
}

private struct RecordingStatePill: View {
    let isRecording: Bool
    var body: some View {
        HStack(spacing: 5) {
            RecordingDot(isRecording: isRecording)
            Text(isRecording ? "REC" : "SAVED")
                .font(.caption.weight(.bold))
                .foregroundStyle(isRecording ? Color.red : Color.gray)
        }
    }
}

/// Both intents are `LiveActivityIntent`s performed in the app's process —
/// alive for the whole drive thanks to background location (see
/// Shared/RecordingIntents.swift for the honest process model).
private struct RecordingActivityButtons: View {
    var body: some View {
        HStack(spacing: 10) {
            Button(intent: PinSyncMarkerIntent()) {
                Label("Marker", systemImage: "mappin.and.ellipse")
                    .font(.caption.weight(.semibold))
                    .frame(maxWidth: .infinity)
            }
            .tint(.indigo)
            Button(intent: StopRecordingIntent()) {
                Label("Stop", systemImage: "stop.fill")
                    .font(.caption.weight(.semibold))
                    .frame(maxWidth: .infinity)
            }
            .tint(.red)
        }
        .buttonStyle(.bordered)
    }
}

/// The lock-screen / StandBy / CarPlay-Dashboard banner. Adapts to the compact
/// `.small` activity family (CarPlay Dashboard, Watch Smart Stack) by dropping
/// to a single glanceable row + actions; falls back to the full banner for the
/// standard lock-screen (`.medium`) presentation.
private struct RecordingActivityBanner: View {
    @Environment(\.activityFamily) private var activityFamily
    let state: RecordingActivityAttributes.ContentState

    var body: some View {
        switch activityFamily {
        case .small:
            small
        default:
            medium
        }
    }

    /// Full lock-screen / StandBy banner.
    private var medium: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                RecordingStatePill(isRecording: state.isRecording)
                Spacer()
                elapsedText(state)
                    .font(.system(.title3, design: .monospaced).weight(.semibold))
                    .foregroundStyle(.white)
            }
            Text(sampleCountsLine(state))
                .font(.caption)
                .foregroundStyle(.white.opacity(0.65))
            if state.isRecording {
                RecordingActivityButtons()
            }
        }
        .padding(14)
    }

    /// Compact row for the CarPlay Dashboard / Watch Smart Stack: status +
    /// live-ticking elapsed on one line, then the same in-process actions so the
    /// driver can stop or pin a marker without reaching for the phone.
    private var small: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                RecordingStatePill(isRecording: state.isRecording)
                Spacer()
                elapsedText(state)
                    .font(.system(.body, design: .monospaced).weight(.semibold))
                    .foregroundStyle(.white)
            }
            if state.isRecording {
                RecordingActivityButtons()
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }
}
