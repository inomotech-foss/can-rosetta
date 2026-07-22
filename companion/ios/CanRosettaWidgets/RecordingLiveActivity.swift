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
/// `RecordingController` owns the lifecycle (request → throttled updates →
/// end); this file is pure presentation over
/// `RecordingActivityAttributes.ContentState`.
struct RecordingLiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: RecordingActivityAttributes.self) { context in
            // Lock screen / StandBy / CarPlay Dashboard banner.
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

/// The lock-screen / StandBy / CarPlay-Dashboard banner.
private struct RecordingActivityBanner: View {
    let state: RecordingActivityAttributes.ContentState

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                HStack(spacing: 5) {
                    RecordingDot(isRecording: state.isRecording)
                    Text(state.isRecording ? "Recording drive" : "Drive saved")
                        .font(.caption.weight(.bold))
                        .foregroundStyle(state.isRecording ? Color.red : Color.gray)
                }
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
}
