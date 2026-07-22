import WidgetKit
import SwiftUI

/// Home-screen / lock-screen / CarPlay-Dashboard status widget: recording
/// state, a live-ticking elapsed timer, IMU/GPS sample counts and GPS accuracy,
/// plus an interactive **Stop** button (iOS 17 `Button(intent:)`).
///
/// Data flows one way: `RecordingController` publishes a `RecordingSnapshot`
/// into the shared app group (throttled ~1 Hz) and nudges `WidgetCenter` on
/// state changes; the provider here only ever reads. The elapsed timer does not
/// depend on reloads — it is a `Text(_:style: .timer)` anchored at the
/// snapshot's start time, so it ticks locally between refreshes.
struct RecordingStatusWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: RecordingWidgetBridge.statusWidgetKind,
                            provider: RecordingStatusProvider()) { entry in
            RecordingStatusView(entry: entry)
                // Dark card matching the app's midnight theme (the app's
                // `Theme` is not compiled into this target — one literal here
                // beats dragging the whole view layer across).
                .containerBackground(for: .widget) { Color(red: 0.04, green: 0.05, blue: 0.09) }
        }
        .configurationDisplayName("CAN-Rosetta recording")
        .description("Shows the drive recording status and lets you stop it.")
        .supportedFamilies([.systemSmall, .accessoryRectangular, .accessoryCircular, .accessoryInline])
    }
}

// MARK: - Timeline

struct RecordingStatusEntry: TimelineEntry {
    let date: Date
    let snapshot: RecordingSnapshot?
}

struct RecordingStatusProvider: TimelineProvider {

    func placeholder(in context: Context) -> RecordingStatusEntry {
        RecordingStatusEntry(date: .now, snapshot: RecordingSnapshot.placeholder)
    }

    func getSnapshot(in context: Context, completion: @escaping (RecordingStatusEntry) -> Void) {
        // Gallery previews get the placeholder; a real snapshot request gets
        // whatever the app last published (nil on a fresh install).
        let snapshot = context.isPreview ? RecordingSnapshot.placeholder : RecordingSnapshot.load()
        completion(RecordingStatusEntry(date: .now, snapshot: snapshot))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<RecordingStatusEntry>) -> Void) {
        let entry = RecordingStatusEntry(date: .now, snapshot: RecordingSnapshot.load())
        // One entry per timeline: the app pushes reloads on start/stop and
        // sparsely mid-drive. While recording, additionally self-refresh every
        // minute so the sample counters cannot go arbitrarily stale if the
        // app's nudges get budget-throttled; when idle there is nothing to
        // refresh for (`.never` — the next change comes as an app nudge).
        let recording = entry.snapshot?.isRecording ?? false
        let policy: TimelineReloadPolicy = recording ? .after(.now.addingTimeInterval(60)) : .never
        completion(Timeline(entries: [entry], policy: policy))
    }
}

extension RecordingSnapshot {
    /// What the widget gallery shows — a plausible mid-drive state.
    static let placeholder = RecordingSnapshot(
        sessionId: "00000000-0000-0000-0000-000000000000",
        isRecording: true,
        startedAtUTC: Date().timeIntervalSince1970 - 754,
        motionCount: 75_400,
        locationCount: 754,
        gpsAccuracyM: 5,
        updatedAtUTC: Date().timeIntervalSince1970)
}

// MARK: - Views

struct RecordingStatusView: View {
    @Environment(\.widgetFamily) private var family
    let entry: RecordingStatusEntry

    private var snapshot: RecordingSnapshot? { entry.snapshot }
    private var isRecording: Bool { snapshot?.isRecording ?? false }

    /// Anchor for the live-ticking timer.
    private var startDate: Date? {
        snapshot?.startedAtUTC.map { Date(timeIntervalSince1970: $0) }
    }

    /// The app writes at ~1 Hz while recording; a snapshot much older than the
    /// widget's own refresh cadence means the app stopped publishing (killed
    /// mid-drive) and the "REC" claim can no longer be trusted.
    private var isStale: Bool {
        guard let snapshot, snapshot.isRecording else { return false }
        return entry.date.timeIntervalSince1970 - snapshot.updatedAtUTC > 180
    }

    var body: some View {
        switch family {
        case .accessoryInline:
            inline
        case .accessoryCircular:
            circular
        case .accessoryRectangular:
            rectangular
        default:
            systemSmall
        }
    }

    // MARK: systemSmall — the CarPlay Dashboard / home-screen card

    private var systemSmall: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 5) {
                Circle()
                    .fill(isRecording ? Color.red : Color.gray)
                    .frame(width: 8, height: 8)
                Text(isRecording ? (isStale ? "REC?" : "REC") : "IDLE")
                    .font(.caption.weight(.bold))
                    .foregroundStyle(isRecording ? Color.red : Color.gray)
                Spacer()
            }
            if isRecording, let startDate {
                Text(startDate, style: .timer)
                    .font(.system(.title2, design: .monospaced).weight(.semibold))
                    .foregroundStyle(.white)
                Text(countsLine)
                    .font(.caption2)
                    .foregroundStyle(.white.opacity(0.65))
                Spacer(minLength: 0)
                // Runs `StopRecordingIntent` in the app's process (see
                // Shared/RecordingIntents.swift for the honest process model).
                Button(intent: StopRecordingIntent()) {
                    Label("Stop", systemImage: "stop.fill")
                        .font(.caption.weight(.semibold))
                        .frame(maxWidth: .infinity)
                }
                .tint(.red)
            } else {
                Text("Not recording")
                    .font(.footnote)
                    .foregroundStyle(.white.opacity(0.65))
                Spacer(minLength: 0)
                Text("Open to start a drive")
                    .font(.caption2)
                    .foregroundStyle(.white.opacity(0.4))
            }
        }
        // Starting is deliberately not an intent: it needs the app's pre-flight
        // flow (permissions, mount check, pairing). Tapping the idle widget
        // deep-links into the containing app instead — `widgetURL` routes there
        // directly, no custom URL scheme registration required.
        .widgetURL(isRecording ? nil : URL(string: "canrosetta://record"))
    }

    // MARK: Accessory (lock screen / watch-style) families

    private var inline: some View {
        // Single line; the timer keeps ticking without reloads.
        Group {
            if isRecording, let startDate {
                Text("REC ") + Text(startDate, style: .timer)
            } else {
                Text("CAN-Rosetta idle")
            }
        }
    }

    private var circular: some View {
        VStack(spacing: 1) {
            Image(systemName: isRecording ? "record.circle.fill" : "record.circle")
                .font(.title3)
            if isRecording, let startDate {
                Text(startDate, style: .timer)
                    .font(.system(.caption2, design: .monospaced))
                    .multilineTextAlignment(.center)
            }
        }
    }

    private var rectangular: some View {
        VStack(alignment: .leading, spacing: 1) {
            HStack(spacing: 4) {
                Circle()
                    .fill(isRecording ? Color.red : Color.gray)
                    .frame(width: 6, height: 6)
                Text(isRecording ? "Recording" : "Not recording")
                    .font(.headline)
            }
            if isRecording, let startDate {
                Text(startDate, style: .timer)
                    .font(.system(.body, design: .monospaced))
                Text(countsLine)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
    }

    // MARK: Shared bits

    private var countsLine: String {
        guard let snapshot else { return "—" }
        var line = "\(snapshot.motionCount.formattedCompact) IMU · \(snapshot.locationCount.formattedCompact) GPS"
        if let acc = snapshot.gpsAccuracyM {
            line += String(format: " ±%.0f m", acc)
        }
        return line
    }
}

extension Int {
    /// "75400" → "75.4k": the widget card is small and exact counts belong in
    /// the app; the widget only needs to show that samples keep flowing.
    var formattedCompact: String {
        self >= 10_000 ? String(format: "%.1fk", Double(self) / 1000) : String(self)
    }
}
