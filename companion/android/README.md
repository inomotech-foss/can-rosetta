# CAN-Rosetta Companion (Android)

The Android half of a CAN-Rosetta recording, with **feature parity to the iOS
companion**. While you drive, it captures the *known side of the Rosetta stone* —
high-rate IMU, GPS, and (optionally) a video and/or full-resolution stills of the
dashboard — all honestly UTC-timestamped, and exports a *session part* in the
[shared data format](../../docs/data-format.md). On the server that part is
merged, by `session_id`, with the AutoPi's CAN log.

Like the iOS app it is intentionally dumb: **capture accurately, timestamp
honestly, don't interpret.** No filtering, no resampling — raw samples at their
true acquisition time. Alignment and decoding are the server's job.

## What it records

| Output                      | Source (Android)                        | Rate      | Schema |
|-----------------------------|-----------------------------------------|-----------|--------|
| `phone/motion.jsonl`        | `SensorManager` (fused per-sensor)      | ~100 Hz   | [`motion.record`](../../schemas/motion.record.schema.json) |
| `phone/location.jsonl`      | `FusedLocationProviderClient`           | 1–10 Hz   | [`location.record`](../../schemas/location.record.schema.json) |
| `phone/video.mp4`           | CameraX `VideoCapture`/`Recorder`       | ~30 fps   | (optional) |
| `phone/video_index.jsonl`   | CameraX `ImageAnalysis` frame timestamps| per frame | (optional) |
| `phone/photos/NNNNNN.jpg`   | CameraX `ImageCapture` full-res         | ~0.5 s    | (optional) |
| `phone/photos_index.jsonl`  | photo capture time → `t_utc`            | per still | [`photo_index.record`](../../schemas/photo_index.record.schema.json) |
| `manifest.json`             | assembled at stop                       | —         | [`manifest`](../../schemas/manifest.schema.json) |

### Field mapping (matches the schemas exactly)

Motion — one record per `TYPE_LINEAR_ACCELERATION` event, carrying the latest
cached value of each other sensor (Android delivers sensors independently, unlike
iOS's fused `CMDeviceMotion`):

- `acc` = `TYPE_LINEAR_ACCELERATION` (m/s² ÷ 9.80665) → **g**, gravity removed
- `gravity` = `TYPE_GRAVITY` (m/s² ÷ 9.80665) → **g**
- `rot` = `TYPE_GYROSCOPE` rad/s `(x, y, z)`
- `att` = `TYPE_ROTATION_VECTOR` → `getRotationMatrixFromVector` + `getOrientation`,
  reordered to `[roll, pitch, yaw]` rad
- `mag` = `TYPE_MAGNETIC_FIELD` µT, **null** until the first sample / if absent
- `t_utc` on every record

Location (`Location` → record): `lat`, `lon`, `alt`; `speed` (m/s, `-1` if
unknown); `course` (deg from true north, `-1` if unknown); `h_acc`, `v_acc` (m);
`t_utc`.

All JSON is written with the bundled `org.json` (no kotlinx-serialization), field
names are the schema wire names directly (`t_utc`, `h_acc`, `v_acc`, `acc`,
`gravity`, `rot`, `att`, `mag`, `schema_version: "1.0.0"`).

## Timestamping (the whole ballgame)

Every record carries `t_utc` — Unix epoch seconds as a `Double` (UTC). We do
**not** read wall clock per sample. `time/Clock.kt` anchors **once** at recording
start (`time/TimeMath.kt` holds the pure math):

```
bootWallClockUtc = System.currentTimeMillis()/1000 − SystemClock.elapsedRealtimeNanos()/1e9
t_utc            = bootWallClockUtc + sampleElapsedNanos/1e9
```

- **Motion** uses `SensorEvent.timestamp` (monotonic nanoseconds).
- **Video / photos** use CameraX `ImageInfo.timestamp` (monotonic nanoseconds),
  so a still, a video frame and an IMU sample taken at the same instant share one
  comparable `t_utc` domain.
- **Location** uses each fix's own `Location.time` (wall-clock ms), the best time
  we have for a GPS sample.

Because the anchor is captured once, a mid-drive NTP/carrier step does not change
the *relative* spacing between samples — only a constant offset the server
estimates and removes during fine alignment. The manifest reports
`clock.source = "gps"` with `err_est_s = 0.1` (honest caveat: Android does not
expose raw GNSS/PPS time to apps, so the absolute offset is really the system
clock's).

### Video indexing

`VideoCapture`/`Recorder` writes the MP4 internally and exposes no per-encoded-
frame callback, so a true per-frame PTS is impractical. We therefore bind an
`ImageAnalysis` alongside `VideoCapture` and **index at the analyzer cadence**:
each analyzed frame writes `{ frame, pts, t_utc }`, where `t_utc` comes from
`ImageInfo.timestamp` via the shared `Clock` and `pts` is seconds relative to the
first analyzed frame. This is the fallback the data-format doc explicitly permits.

### Hybrid still capture

Full-resolution JPEGs are captured on a timer (default 0.5 s) via CameraX
`ImageCapture` (`OnImageCapturedCallback`, so we read the real capture timestamp
and exact pixel `w`/`h`) into `phone/photos/`, indexed by `photos_index.jsonl`.
Video and stills coexist on **one** camera: `VideoCapture` + `ImageAnalysis` +
`ImageCapture` are bound to the same `ProcessCameraProvider` lifecycle. If the
device cannot bind the full set, the app **degrades gracefully** — it drops use
cases from the tail (stills first, then the video index) until something binds,
keeping video highest priority. Stills are **not** listed in `manifest.json` (the
`streams[].kind` enum has no photo kind); they self-describe via
`photos_index.jsonl`, exactly like iOS.

Video is recorded **without audio** to avoid needing the `RECORD_AUDIO`
permission.

## Remote control of the AutoPi

`remote/EdgeControlClient.kt` is an OkHttp client for the
[control protocol](../../docs/control-protocol.md): `health()`, `time()`,
`status()`, `createSession()`, `discover(mode)`, `startLog()`, `stopLog()`,
`run(mode)` and a WebSocket `events()` stream (exposed as a coroutine `Flow`).
HTTP requests carry `Authorization: Bearer <token>`; the WebSocket carries the
token both as that header and a `?token=` query param.

`remote/EdgeConnection.kt` owns host/token/mode (persisted in SharedPreferences),
the measured clock offset, live edge status and the coordinated start/stop:

1. **Pair / connect** — enter host (e.g. `http://192.168.4.1:8765`) + token;
   `GET /api/health` drives the status.
2. **Time sync** — `GET /api/time` a few times (Cristian's algorithm), keep the
   smallest-round-trip sample, estimate
   `edge_utc_offset_est_s = edge_utc − companion_utc`.
3. **Investigation** — pick fast/slow, `POST /api/session` (shared session id +
   offset + `clock_source:"gps"`), then `POST /api/discover {mode}`.
4. **Coordinated recording** — one action does `POST /api/session` →
   `POST /api/log/start` on the edge **first**, then starts the phone's own
   recording with the **same `session_id`** (so we never record alone if the edge
   rejects the request). Stop stops both.
5. **Live status** — subscribes to `GET /api/ws`; falls back to polling
   `GET /api/status` every 2 s if the socket drops.

The shared `session_id` is single-sourced from `RecordingController.sessionId`
(the phone mints it).

## Build

No Android Studio required for CI; the pinned wrapper fetches everything.

```sh
cd companion/android
./gradlew assembleDebug        # build the debug APK
./gradlew testDebugUnitTest    # run the pure-JVM unit tests (TimeMathTest)
```

Requires JDK 17. Version stack (pinned, do not deviate):

- Gradle 8.7, Android Gradle Plugin 8.5.2, Kotlin 1.9.24
- `compileSdk 34`, `minSdk 26`, `targetSdk 34`
- Compose compiler 1.5.14, Compose BOM 2024.06.00, Material3
- CameraX 1.3.4, play-services-location 21.3.0, OkHttp 4.12.0,
  kotlinx-coroutines 1.8.1

## Permissions

Requested at runtime (main screen on launch):

- `CAMERA` — dashboard video and/or full-resolution stills
- `ACCESS_FINE_LOCATION` — GPS track (`phone/location.jsonl`)

Declared in the manifest: `CAMERA`, `ACCESS_FINE_LOCATION`, `INTERNET`
(+ `ACCESS_NETWORK_STATE`). Camera and GPS features are `required="false"` so the
app installs on devices without them (it still records IMU). No `RECORD_AUDIO` —
video is silent by design.

## How the session maps to the shared format

```
sessions/session-<id>/          (app files dir)
├── manifest.json                role:"companion", kind:"android", clock.source:"gps"
└── phone/
    ├── motion.jsonl             motion stream
    ├── location.jsonl           location stream
    ├── video.mp4 + video_index.jsonl   video stream (if filmed + bound)
    └── photos/ + photos_index.jsonl    self-describing stills (if captured + bound)
```

On **Stop**, the app writes `manifest.json` and zips the whole `session-<id>/`
directory into `cache/exports/session-<id>.zip`, offered through the system share
sheet (via a `FileProvider`). Sessions also persist under the app's files dir.

## Privacy

**GPS traces and dashboard imagery are personal data.** Recordings never leave
the device automatically — you choose when to share each archive. Video is off by
default; still capture is on by default (and can be turned off).
