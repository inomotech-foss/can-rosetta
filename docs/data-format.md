# Session data format

Everything in CAN-Rosetta revolves around a **session**: one continuous recording
of a vehicle, captured simultaneously by the edge device (AutoPi) and the
companion phone. The server never talks to the vehicle directly — it only ever
consumes sessions. Getting this format right is what lets three independently
developed components (edge, companion, server) interoperate.

A session is a directory (or a `.tar.zst` archive of that directory) with a
stable layout:

```
session-<uuid>/
├── manifest.json            # who/what/when + clock-sync metadata + stream index
├── can/
│   ├── frames.parquet       # every CAN frame observed (the raw haystack)
│   └── discovery.json       # what the edge probing found (OBD/UDS/plain-CAN)
├── phone/
│   ├── motion.jsonl         # IMU: accel, gyro, attitude, magnetometer
│   ├── location.jsonl       # GPS: lat/lon/alt/speed/course
│   ├── video.mp4            # optional dashboard video
│   └── video_index.jsonl    # per-frame presentation timestamps for video.mp4
└── labels/                  # optional ground-truth, produced later
    ├── dashboard_ocr.jsonl  # values read off the filmed dashboard
    └── annotations.json     # human corrections / known signal mappings
```

All files are UTF-8. All arrays-of-records are newline-delimited JSON (`.jsonl`)
except the CAN frame table, which is columnar Parquet because it is by far the
largest stream (hundreds of thousands to millions of rows per drive).

JSON Schemas for every file live in [`/schemas`](../schemas) and are validated in
CI. The schemas are the normative spec; this document is the explanation.

## Time is the whole ballgame

The entire project depends on aligning two clocks — the vehicle bus (seen by the
AutoPi) and the phone sensors — precisely enough that a bump in the road shows up
in the IMU and in the CAN data at "the same" instant. We handle time in layers:

- **`t_utc`** — Unix epoch seconds, `float64`, UTC. Every record in every stream
  carries one. This is the *lingua franca* used for coarse alignment.
- **`t_mono`** — a device-local monotonic clock in seconds, `float64`, present on
  edge streams. Immune to NTP step corrections mid-drive, so it preserves
  *relative* spacing between CAN frames even if wall-clock jumps.
- **Clock-sync block** in `manifest.json` records each device's clock source
  (`ntp`, `gps`, `manual`), the estimated offset to UTC, and the estimated
  one-way error. The server uses this as the *prior* for fine alignment.

Coarse alignment uses `t_utc`. The server then refines it (sub-100 ms) by
cross-correlating physically redundant signals — e.g. OBD vehicle speed against
GPS ground speed, or a candidate longitudinal-accel CAN signal against the phone
accelerometer. See [`methodology.md`](methodology.md#stage-2-time-alignment).

> **Rule:** producers never resample or interpolate. Record raw samples at their
> true acquisition time. Alignment and resampling are the server's job.

## `manifest.json`

```jsonc
{
  "schema_version": "1.0.0",
  "session_id": "9f2c...-uuid",
  "created_utc": 1752624000.0,
  "vehicle": {
    "make": "VW", "model": "Golf", "year": 2019,
    "vin_hash": "sha256:...",        // hashed, never store a raw VIN
    "notes": "diesel, DSG"
  },
  "devices": [
    {
      "role": "edge",                 // "edge" | "companion"
      "kind": "autopi",
      "id": "autopi-abcd1234",
      "sw_version": "can-rosetta-edge/0.1.0",
      "clock": { "source": "ntp", "utc_offset_est_s": 0.0, "err_est_s": 0.03 }
    },
    {
      "role": "companion",
      "kind": "ios",
      "id": "iphone-1",
      "sw_version": "can-rosetta-companion/0.1.0",
      "clock": { "source": "gps", "utc_offset_est_s": 0.0, "err_est_s": 0.02 }
    }
  ],
  "streams": [
    { "path": "can/frames.parquet", "kind": "can_frames", "rows": 1284551,
      "t_start_utc": 1752624001.2, "t_end_utc": 1752625800.7 },
    { "path": "phone/motion.jsonl", "kind": "motion", "rows": 179940 },
    { "path": "phone/location.jsonl", "kind": "location", "rows": 1799 },
    { "path": "can/discovery.json", "kind": "discovery" },
    { "path": "phone/video.mp4", "kind": "video",
      "index": "phone/video_index.jsonl" }
  ],
  "sync_markers": [
    // optional shared events used to pin the two clocks together, e.g. the
    // driver flashing the brakes 3x at session start (visible in video, in
    // CAN brake frames, and as a deceleration in the IMU).
    { "kind": "brake_pulse", "t_utc": 1752624003.0, "count": 3 }
  ]
}
```

## `can/frames.parquet`

One row per observed CAN frame. Columns (nullable noted):

| column        | type    | notes |
|---------------|---------|-------|
| `t_mono`      | float64 | edge monotonic seconds; primary ordering key |
| `t_utc`       | float64 | edge wall-clock estimate |
| `channel`     | string  | e.g. `can0`, `swcan`, `obd` |
| `arb_id`      | uint32  | arbitration (CAN) ID |
| `is_extended` | bool    | 29-bit vs 11-bit ID |
| `dlc`         | uint8   | data length code (0–8, or up to 64 for CAN-FD) |
| `data`        | binary  | raw payload bytes (length == dlc) |
| `direction`   | string  | `rx` (sniffed) or `tx` (probe we sent) |
| `probe_id`    | string? | if `tx`/its response: links to a `discovery.json` probe |

Passively sniffed traffic is `rx` with a null `probe_id`. Frames generated by the
discovery brute-forcer, and the responses they elicit, carry a `probe_id` so the
server can separate *organic* bus traffic from *induced* traffic.

## `can/discovery.json`

Produced by the edge during the discovery phase (see
[edge README](../edge/autopi/README.md)). Records what protocols/signals the
device could actively find, which enormously shrinks the server's search space.

```jsonc
{
  "schema_version": "1.0.0",
  "obd": {
    "supported_pids": ["0x0C", "0x0D", "0x05", "..."],   // mode 01
    "samples": [
      { "mode": 1, "pid": "0x0D", "name": "vehicle_speed",
        "t_utc": 1752624010.0, "raw": "1a", "value": 26.0, "unit": "km/h" }
    ]
  },
  "uds": {
    "responding_dids": ["0xF190", "0x..."],
    "ecus": [ { "tx_id": "0x7E0", "rx_id": "0x7E8", "dids": ["0xF190"] } ]
  },
  "plain_can": {
    "arb_ids": [ { "arb_id": "0x3C0", "count": 5123, "period_ms_est": 20,
                   "changing_bytes": [0,1,4] } ]
  }
}
```

`discovery.json` is a *catalog with live samples*. Every OBD/UDS value the device
successfully read while driving is a labelled datapoint — ground truth the server
can use directly, and a decoded reference to hunt for inside the *plain* CAN
haystack (the same speed value almost certainly also lives, unlabelled, in some
periodic broadcast frame).

## `phone/motion.jsonl`

One JSON object per line, sampled at 50–100 Hz. Fields mirror iOS CoreMotion
`CMDeviceMotion` but are named platform-neutrally.

```jsonc
{ "t_utc": 1752624001.240,
  "acc": [0.01, -0.02, 0.98],        // user acceleration, g (gravity removed)
  "gravity": [0.0, 0.0, -1.0],       // g
  "rot": [0.001, 0.0, 0.002],        // rotation rate, rad/s (x,y,z)
  "att": [0.0, 0.0, 1.57],           // attitude roll,pitch,yaw, rad
  "mag": [22.1, -5.0, 41.3] }        // calibrated magnetic field, µT (nullable)
```

The device's own frame is documented in `manifest.devices[].mount` when known
(phone orientation in the cradle); otherwise the server estimates the
phone→vehicle rotation from the gravity vector plus GPS heading.

## `phone/location.jsonl`

One JSON object per line, sampled at 1–10 Hz.

```jsonc
{ "t_utc": 1752624001.0,
  "lat": 48.13712, "lon": 11.57550, "alt": 519.4,
  "speed": 7.3,          // m/s over ground, -1 if unknown
  "course": 92.4,        // degrees from true north, -1 if unknown
  "h_acc": 4.0, "v_acc": 6.0 }   // accuracy estimates, meters
```

## `phone/video.mp4` + `phone/video_index.jsonl`

The video is optional but powerful: it lets us OCR the dashboard to obtain ground
truth for signals the OBD/UDS layer never exposes (fuel gauge position, warning
lamps, gear indicator). Because container timestamps are unreliable across
players, we ship an explicit index:

```jsonc
{ "frame": 0, "pts": 0.0, "t_utc": 1752624001.033 }
{ "frame": 1, "pts": 0.0333, "t_utc": 1752624001.066 }
```

## `labels/` (server-side, optional)

Not produced in-vehicle. The server (or a human) writes these back into the
session as it learns:

- `dashboard_ocr.jsonl` — `{ "t_utc":…, "field":"speed", "value":60, "conf":0.9 }`
- `annotations.json` — confirmed mappings, e.g. "arb_id 0x3C0 bytes 1–2,
  big-endian, scale 0.01 == vehicle speed", used both as training labels and as a
  regression fixture.

## Versioning

Every file carries `schema_version` (semver). The server refuses a session whose
`schema_version` major differs from what it supports, and warns on minor skew.
Adding an optional field is a minor bump; renaming/removing or changing a type is
a major bump.
