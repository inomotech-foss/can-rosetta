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

Set your Apple Developer team in `project.yml` (`DEVELOPMENT_TEAM`) or in Xcode's
Signing & Capabilities, then build/run on a device (sensors and camera do not
work in the Simulator).

### CI (simulator build, no signing)

CI generates the project and builds it for the iOS Simulator without code
signing (the target sets `CODE_SIGNING_REQUIRED=NO` / `CODE_SIGN_IDENTITY=""`,
and `project.yml` defines a shared scheme named `CanRosettaCompanion`):

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
- `NSCameraUsageDescription` — dashboard video
- `UIBackgroundModes: [location]` — keep recording when backgrounded/screen off
- `UIFileSharingEnabled` / `LSSupportsOpeningDocumentsInPlace` — sessions visible in the Files app

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

**GPS traces and dashboard video are personal data.** A location trace reveals
where you live and drive; dashboard video may capture surroundings, plates, or
occupants. Recordings never leave the device automatically — you choose when and
where to share each session archive. Video is off by default. No raw VIN is ever
stored (the format uses a hashed `vin_hash`, populated server-side).

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
