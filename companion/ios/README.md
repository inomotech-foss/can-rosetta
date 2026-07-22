# CAN-Rosetta Companion (iOS)

The iPhone half of a CAN-Rosetta recording. While you drive, it captures the
**known side of the Rosetta stone** — high-rate IMU, GPS, and (optionally) a
video of the dashboard — all honestly UTC-timestamped, and exports a *session
part* in the [shared data format](../../docs/data-format.md). On the server that
part is merged, by `session_id`, with the AutoPi's CAN log.

The app is intentionally dumb: **capture accurately, timestamp honestly, don't
interpret.** No filtering, no resampling — raw samples at their true acquisition
time. Alignment and decoding are the server's job.

## What it records

| Output                      | Source (iOS)                    | Rate      | Schema |
|-----------------------------|---------------------------------|-----------|--------|
| `phone/motion.jsonl`        | `CMMotionManager` device motion | ~100 Hz   | [`motion.record`](../../schemas/motion.record.schema.json) |
| `phone/location.jsonl`      | `CLLocationManager` (best accy) | 1–10 Hz   | [`location.record`](../../schemas/location.record.schema.json) |
| `phone/video.mp4`           | `AVCaptureSession` rear camera  | ~30 fps   | (optional) |
| `phone/video_index.jsonl`   | capture PTS → `t_utc`           | per frame | (optional) |
| `phone/photos/NNNNNN.jpg`   | `AVCapturePhotoOutput` full-res | ~0.5 s    | (optional) |
| `phone/photos_index.jsonl`  | photo capture time → `t_utc`    | per still | [`photo_index.record`](../../schemas/photo_index.record.schema.json) |
| `manifest.json`             | assembled at stop               | —         | [`manifest`](../../schemas/manifest.schema.json) |

### Field mapping (must match the schemas exactly)

Motion (`CMDeviceMotion` → record):

- `acc` = `userAcceleration` in g (gravity removed)
- `gravity` = `gravity` in g
- `rot` = `rotationRate` in rad/s `(x, y, z)`
- `att` = attitude `[roll, pitch, yaw]` in rad
- `mag` = calibrated `magneticField.field` in µT, **null** when uncalibrated
- `t_utc` on every record

Location (`CLLocation` → record):

- `lat`, `lon`, `alt`; `speed` (m/s, `-1` if unknown); `course`
  (deg from true north, `-1` if unknown); `h_acc`, `v_acc` (m); `t_utc`
- `produced_by_accessory` = `CLLocation.sourceInformation.isProducedByAccessory`
  (optional; omitted when iOS reports no source info) — flags fixes that came
  from the car, not the phone (wireless CarPlay feeds vehicle GNSS into
  CoreLocation), so the server can downgrade them as non-independent references

Idiomatic camelCase Swift properties (`tUtc`, `hAcc`) are serialised with
`JSONEncoder.keyEncodingStrategy = .convertToSnakeCase`, so they land on the
wire as `t_utc`, `h_acc`, `schema_version`, `utc_offset_est_s`, etc.

## Timestamping (the whole ballgame)

Every record carries `t_utc` — Unix epoch seconds as a `Double` (UTC).

We do **not** call `Date()` per sample. Instead `Time/Clock.swift` anchors
**once** at recording start:

```
bootWallClockUTC = Date() − ProcessInfo.systemUptime   // captured once
t_utc            = bootWallClockUTC + sampleUptime      // per sample
```

Every sensor sample already carries a device-uptime timestamp on a monotonic
clock (`CMLogItem.timestamp` for motion; capture host-time PTS for video)
measured against the same boot instant. Adding the fixed anchor means a later
NTP/carrier time correction mid-drive does **not** change the *relative* spacing
between our samples — only a constant offset, which the server estimates and
removes during fine alignment. This is the "monotonic-anchored wall clock" the
data-format doc asks producers for.

- **Motion / video** use the monotonic anchor (immune to mid-drive steps).
- **Location** uses each fix's own `CLLocation.timestamp` — CoreLocation stamps
  fixes at acquisition and it is the best time we have for a GPS sample.

The manifest reports `clock.source = "gps"` (full-accuracy GNSS runs the whole
session). **Honest caveat:** iOS does not expose raw GNSS/PPS time to apps, so
the *absolute* offset is really the (NTP/carrier-disciplined) system clock; we
report `err_est_s = 0.1` rather than claiming sub-100 ms GPS-locked accuracy.
See TODOs.

### Video indexing

MP4 container timestamps are unreliable across players, so we ship an explicit
index. For each encoded frame we write `{ frame, pts, t_utc }` where `pts` is
seconds relative to the first written frame and `t_utc` comes from the capture
sample buffer's presentation timestamp (host-time clock domain) run through the
same `Clock`. Frames are appended to `AVAssetWriter` in real time; late frames
are dropped by the capture output rather than stalling the pipeline.

### Hybrid still capture

Video trades spatial resolution for frame rate: great for a turn-signal blink or
a needle sweep, poor for OCR of small dashboard digits through HEVC/H.264
compression. So the app *also* captures periodic **full-resolution JPEG stills**
(`Recording/PhotoCapture.swift`) into `phone/photos/`, indexed by
`phone/photos_index.jsonl` (`{ t_utc, path, w, h }`, see
[`photo_index.record`](../../schemas/photo_index.record.schema.json)). The server
routes numeric/gear OCR to the nearest still (accuracy) and telltales/needles to
the video (temporal density).

- Enabled by the **Capture stills** toggle (on by default); interval is
  `photoIntervalSeconds` (default 0.5 s).
- An `AVCapturePhotoOutput` is attached to the **same** `AVCaptureSession` the
  video uses, so filming and stills run together off one camera without
  interrupting the video. With video off, still capture stands up its own
  `.photo`-preset session.
- The output is configured for maximum still resolution
  (`maxPhotoDimensions` = the active format's largest supported size, quality
  prioritization). Each still's `t_utc` comes from `AVCapturePhoto.timestamp`
  (the same host-time clock domain as the video PTS) run through the same
  `Clock`, so stills, video frames and IMU samples share one `t_utc` domain.
- Setup degrades gracefully: if the session cannot add a photo output (e.g. a
  device/multicam limit) or the camera is unavailable, stills are disabled with a
  log and the rest of the recording is unaffected. Stills are **not** listed in
  `manifest.json` (the manifest `streams[].kind` enum has no photo kind); they are
  self-describing via `photos_index.jsonl`, matching the data-format spec.

## Drive flow (UI)

The app presents a dark, native SwiftUI **five-screen flow** — a small state
machine (`Views/DriveFlow.swift`, a `@StateObject` in `ContentView`) that steps
through **Pair → Pre-flight → Recording → Sync marker → Hand-off**. It is a thin
UI layer over the existing controllers: all recording/remote/sensor logic lives
in `RecordingController` and `EdgeConnection`; the flow only navigates and calls
through. Shared styling (b-on "Midnight" palette, cards, buttons, rows) lives in
`Views/Theme.swift` + `Views/FlowComponents.swift`.

The flow runs in one of two **modes** (`DriveFlowModel.PairingMode`): `.paired`
(coordinated with an AutoPi) or `.standalone` (phone-only, no edge). `.standalone`
never configures or starts `EdgeConnection`.

1. **Pair AutoPi** (`PairView`) — a live QR viewfinder (`QRScannerView`,
   `AVCaptureMetadataOutput`/`.qr`) reads a JSON payload
   `{"host":…,"token":…,"session_id":…?}`, configures `EdgeConnection`, then runs
   the existing Cristian time-sync (shows `offset ±NN ms · rtt N ms`). Because a
   **headless AutoPi has no screen to show a QR**, manual **Host + Control token**
   entry is a **first-class option** on this screen (not hidden): a *Pair* button
   runs the same health-check + time-sync and shows the same handshake-complete
   result. The host defaults to the AP guess `http://192.168.4.1:8765` so a user on
   the AutoPi Wi-Fi just adds the token (the installer prints both, plus a QR you
   can scan from an SSH terminal). "Advanced control" opens the full
   `RemoteControlView`. Wi-Fi SSID is shown as `—` (iOS gates SSID behind
   entitlements we don't hold). → *Confirm — arm both recorders* (paired), **or**
   *Record without AutoPi* to enter the flow in `.standalone`.
2. **Pre-flight** (`PreflightView`) — a live checklist: paired (edge health) and
   clocks pinned (Cristian offset) — **both dropped in `.standalone`** — plus GPS
   fix (`CLLocation` horizontal accuracy), motion available, storage (real
   `volumeAvailableCapacityForImportantUsage`), camera (only if *Film dashboard*),
   and phone-mounted (a standby accelerometer monitor computes the RMS of
   acceleration magnitude → steady/`vibration high`). *Start recording* is blocked
   while a blocking check fails and enables itself once the cradle stops rattling.
   → coordinated start when paired (phone-only fallback if the edge is
   unreachable), or a local `RecordingController.start()` in `.standalone`.
3. **Recording** (`RecordingView`) — a blinking REC pill, the session id, the
   link state, the pulsing **HAL-9000 eye**, a big mono `HH:MM:SS` timer, and a
   live stats card (IMU Hz/samples, GPS accuracy/fixes, video fps/frames when
   filming, AutoPi frames). In `.standalone` the *AutoPi · can0* row is hidden and
   the link label reads a muted *phone only*. CAN load is shown `—` (not surfaced
   by the edge status). → *Stop recording* (local-only in `.standalone`).
4. **Sync marker** (`SyncMarkerView`) — "flash the brakes 3×". Pinning writes a
   `sync_marker` (`kind:"brake_pulse"`, `count:3`, `t_utc` now) into the session;
   the manifest/archive are re-written after stop (see `SessionManifest`'s
   `sync_markers`). Honest alignment: the phone flags its own IMU decel spike
   (green), while CAN/video rows read *pending · server aligns*.
5. **Hand-off** (`HandoffView`) — session summary from real counters (drive
   duration + GPS distance, motion samples, location fixes, video frames, the
   exported `.zip` name + size), an honest upload note (the app shares via the
   system share sheet; the AutoPi uploads its own part; the server merges by
   session id), and *Share archive*.

Two animations: `recBlink` (the REC dot, 1.2 s) and `halGlow` (the HAL eye's
scale + red shadow, ~2.4 s), both via `.repeatForever(autoreverses:)`.

## Car projection (CarPlay)

There is **no full CarPlay app** — that needs the
`com.apple.developer.carplay-driving-task` entitlement, whose application is
pending (granted to the developer account, not the repo). What ships is the
entitlement-free fast path, per [`docs/car-projection.md`](../../docs/car-projection.md):

- an **interactive widget** (recording status, live-ticking timer, IMU/GPS
  counters, **Stop** via App Intents; idle it deep-links into the app — start
  needs the pre-flight flow, so it is deliberately *not* a button) and a
  **Live Activity** for the running recording (Stop + Pin sync marker) — from
  **iOS 26** both appear on the **CarPlay Dashboard** as-is. They live in a
  second target, `CanRosettaWidgets` (WidgetKit extension, iOS 17+), built as an
  embedded dependency of the app — same single CI scheme. Only the app process
  sees the sensors: it publishes a compact `RecordingSnapshot` into the App
  Group `group.com.inomotech.canrosetta.companion` (~1 Hz) for the widget
  timeline, and pushes throttled Live Activity updates (significant change or
  ~5 s; elapsed ticks locally in the extension). The buttons are
  `LiveActivityIntent`s (shared types in `CanRosettaCompanion/Shared/`, compiled
  into both targets) performed **in the app's process** — alive for the whole
  drive thanks to background location; a widget-initiated stop is phone-side
  only (a paired AutoPi keeps logging until the hand-off flow stops it);
- **GPS provenance tagging** (`produced_by_accessory`, above): CarPlay forwards
  no vehicle data to apps, but wireless-CarPlay head units *do* fuse vehicle
  GNSS into CoreLocation — the tag keeps that from silently masquerading as an
  independent phone reference.

## Remote control of the AutoPi

The app can steer the AutoPi over its **local HTTP + WebSocket control API**
(see [`control-protocol.md`](../../docs/control-protocol.md)) so the driver can
start an investigation and coordinate recording from the phone. Reach it from the
antenna icon in the top-left of the main screen.

What it does:

1. **Pair / connect** — enter the AutoPi host (e.g. `http://192.168.4.1:8765`)
   and the pre-shared bearer token. Both are persisted in `UserDefaults`.
   `GET /api/health` drives the connection status.
2. **Time sync** — `GET /api/time` a few times (Cristian's algorithm) using the
   app's monotonic-anchored `Clock` for the phone side; keeps the sample with the
   smallest round-trip and estimates `edge_utc_offset_est_s = edge_utc −
   companion_utc`. That offset is sent to the AutoPi so both clocks share a prior.
3. **Investigation** — pick **fast** (catalog scan) or **slow** (brute-force),
   `POST /api/session` (sharing the session id + offset + `clock_source`), then
   `POST /api/discover {mode}`. The discovery summary comes from status / WS.
4. **Coordinated recording** — one "Start recording" action does
   `POST /api/session` → `POST /api/log/start` on the edge, then starts the
   phone's own `RecordingController` **with the same `session_id`** so the server
   merges both parts. "Stop" stops the phone and `POST /api/log/stop`.
5. **Live status** — subscribes to `GET /api/ws`
   (`URLSessionWebSocketTask`, bearer via `?token=`) for edge state, frame count,
   OBD samples and elapsed time, and falls back to polling `GET /api/status`
   every 2 s if the socket drops.

Code:

- `Remote/EdgeControlClient.swift` — stateless async `URLSession` client plus the
  WebSocket `events()` `AsyncThrowingStream`; Codable structs matching the
  protocol JSON via `convertFromSnakeCase` / `convertToSnakeCase`.
- `Remote/EdgeConnection.swift` — `@MainActor ObservableObject` holding
  host/token, the measured offset, live edge status, and the coordinated
  start/stop that drives `RecordingController`.
- `Views/RemoteControlView.swift` — the pairing + control UI.

The **shared `session_id` is single-sourced** from
`RecordingController.sessionId` (the phone mints it); the coordinated start sends
that exact id to the AutoPi via `POST /api/session`.

Local networking to the AutoPi's AP requires `NSAllowsLocalNetworking` and
`NSLocalNetworkUsageDescription`, both declared via `project.yml`.

## Build

Requires macOS with Xcode 15+ and [XcodeGen](https://github.com/yonyz/XcodeGen).

```sh
cd companion/ios
brew install xcodegen        # if needed
xcodegen generate            # produces CanRosettaCompanion.xcodeproj
open CanRosettaCompanion.xcodeproj
```

### Running on a device (signing)

Signing is **automatic** and configured in [`Signing.xcconfig`](Signing.xcconfig),
which `project.yml` wires in via `configFiles`. To run on a device, set your Apple
Developer Team ID in a local, gitignored override (once) — it survives
`xcodegen generate` and is never committed:

```sh
echo 'DEVELOPMENT_TEAM = XXXXXXXXXX' > companion/ios/Signing.local.xcconfig
xcodegen generate && open companion/ios/CanRosettaCompanion.xcodeproj
```

(Find your team under Xcode ▸ Settings ▸ Accounts, or
`security find-identity -v -p codesigning`.) Then build/run on a device from
Xcode — the Apple ID you add in Xcode's Accounts provisions it automatically.
Sensors and the camera do not work in the Simulator. No `CODE_SIGNING_*=NO` is
baked into the project anymore, so device builds sign without any manual edit.

### CI (simulator build, no signing)

CI generates the project and builds it for the iOS Simulator, disabling signing
**on the command line** (`CODE_SIGNING_ALLOWED=NO`) rather than in the project —
so device builds still sign while the Simulator build needs no team:

```sh
cd companion/ios && xcodegen generate
xcodebuild -project CanRosettaCompanion.xcodeproj -scheme CanRosettaCompanion \
  -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' \
  CODE_SIGNING_ALLOWED=NO build
```

The generated `CanRosettaCompanion.xcodeproj` is **not** committed (gitignored);
CI regenerates it from `project.yml`.

- Bundle id: `com.inomotech.canrosetta.companion`
- Deployment target: iOS 16.0
- Device family: iPhone

## Required capabilities / permissions

Declared in `Info.plist`:

- `NSMotionUsageDescription` — IMU
- `NSLocationWhenInUseUsageDescription` / `NSLocationAlwaysAndWhenInUseUsageDescription` — GPS (Always is needed for background recording in a cradle)
- `NSCameraUsageDescription` — dashboard video and/or full-resolution stills
- `UIBackgroundModes: [location]` — keep recording when backgrounded/screen off
- `UIFileSharingEnabled` / `LSSupportsOpeningDocumentsInPlace` — sessions visible in the Files app
- `NSSupportsLiveActivities` — the recording Live Activity (CarPlay Dashboard on iOS 26)
- App Group `group.com.inomotech.canrosetta.companion` (checked-in `.entitlements`
  on both targets, generated from `project.yml`) — the widget's snapshot channel

## Session id handshake

In the full system the `session_id` is agreed with the AutoPi (QR handshake or
manual entry) so both parts merge server-side. The main screen lets you generate
a UUID or type/paste the agreed id before recording, and share it. (A camera-based
QR scan/exchange is a TODO — see below.)

## Export

On **Stop**, the app writes `manifest.json` and zips the whole
`session-<id>/` directory (via `NSFileCoordinator(.forUploading)` — no
third-party zip dependency) into `session-<id>.zip`, offered through the system
share sheet (AirDrop, Files, etc.). Sessions also live under the app's Documents
folder and are reachable from the Files app.

## Privacy

**GPS traces and dashboard imagery are personal data.** A location trace reveals
where you live and drive; dashboard video and stills may capture surroundings,
plates, or occupants. Recordings never leave the device automatically — you
choose when and where to share each session archive. Video is off by default;
still capture is on by default (and can be turned off). No raw VIN is ever stored
(the format uses a hashed `vin_hash`, populated server-side).

## TODOs / caveats for developers

- **GPS-time disciplining:** we cannot access raw GNSS time on iOS; a true
  GPS-locked clock (or a shared sync marker like a triple brake-flash, which the
  format already supports via `sync_markers`) would tighten `err_est_s`.
- **QR handshake** for the session id with the AutoPi is not implemented (manual
  entry / generate only).
- **`vehicle` and `sync_markers`** in the manifest are left empty; add UI to
  capture make/model and to mark sync events.
- **Mount orientation** (`devices[].mount`) is not captured; the server can
  estimate phone→vehicle rotation from gravity + GPS heading in the meantime.
- Long drives produce large files; consider size/segment limits and battery/
  thermal management for multi-hour recordings.
