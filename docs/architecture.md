# Architecture

CAN-Rosetta is three cooperating components joined by one file format. Nothing
talks to anything else over a live API during a drive — the vehicle is a hostile
environment for connectivity — so the coupling is deliberately loose: each tier
produces or consumes **sessions** (see [`data-format.md`](data-format.md)).

```
┌─────────────────────────────┐        ┌──────────────────────────┐
│  Vehicle                     │        │  Driver's pocket         │
│  ┌───────────────────────┐  │        │  ┌────────────────────┐  │
│  │ AutoPi  (edge/autopi) │  │        │  │ iPhone (companion) │  │
│  │  • discovery          │  │        │  │  • CoreMotion IMU  │  │
│  │  • continuous CAN log │  │        │  │  • CoreLocation GPS│  │
│  │  → can/frames.parquet │  │        │  │  • dashboard video │  │
│  │  → can/discovery.json │  │        │  │  → phone/*.jsonl    │  │
│  └───────────┬───────────┘  │        │  └─────────┬──────────┘  │
└──────────────┼──────────────┘        └────────────┼─────────────┘
               │        upload session parts         │
               └───────────────┬─────────────────────┘
                               ▼
                 ┌──────────────────────────────┐
                 │  Server  (server/)           │
                 │   2 align → 3 extract →       │
                 │   4 identify → 5 model        │
                 │   → labels/annotations.json   │
                 │   → exported DBC              │
                 └──────────────────────────────┘
```

## Why three tiers

- **Edge (AutoPi)** is the only thing on the bus. It must be conservative
  (read-only probing), robust to power cuts (append-only logs, resumable brute-
  force), and cheap on CPU/flash. It runs Python — AutoPi's native environment —
  reusing the platform's OBD manager, and falls back to SocketCAN or an
  ELM327/STN serial link through a transport abstraction so the same code runs on
  a laptop with a USB-CAN adapter for development.

- **Companion (iPhone)** provides the *labels*. Its sensors are the known side of
  the Rosetta stone. It is a normal iOS app (SwiftUI + CoreMotion + CoreLocation
  + AVFoundation) that records to the shared format and exports a session part.
  It is intentionally dumb: capture accurately, timestamp honestly, don't
  interpret.

- **Server** does everything that benefits from horsepower, hindsight, and cross-
  session memory: precise alignment, the combinatorial candidate extraction, the
  statistical identification baseline, and the learned foundation model. It is
  the only tier that is allowed to be slow.

## Control channel (phone → AutoPi)

Recording is coordinated over a small **local, offline** control link: the AutoPi
runs an HTTP + WebSocket server on its own WiFi and the phone is the client (see
[control-protocol.md](control-protocol.md)). From the phone the driver creates a
shared session, picks the discovery mode (fast / brute-force), starts the
investigation, and starts/stops recording on both devices at once. The handshake
also carries a **time sync** (Cristian's algorithm) that pins the two clocks
together before the drive, shrinking the residual offset the server must recover.
Getting the phone onto the AutoPi's WiFi is a one-time QR scan — the pairing
payload carries the AP credentials, the app joins programmatically, and the edge
chirps when it is ready (see [connection.md](connection.md)).

This does not reintroduce a server dependency in the vehicle — it is strictly
peer-to-peer between the two devices, and both still record fully to local disk.

## Onboard edge sensors

The AutoPi logs its **own IMU/GPS** (`edge/motion.jsonl`, `edge/location.jsonl`)
beside the CAN bus. Because these are on the *same clock* as the CAN frames, they
give the server motion references with zero cross-device alignment error — the
most reliable way to pin down acceleration/speed signals. The phone's sensors and
video remain valuable for signals the AutoPi can't sense (dashboard-only
indicators) and as an independent cross-check.

While discovering or logging, the AutoPi holds a **wake lock** so its power
manager never sleeps the device mid-recording.

## Car projection (head-unit surface)

Both companion apps also project into the car, for two reasons: the head unit
is the only screen a driver may lawfully glance at, and — on Android — it is a
**fourth reference source**. The Android companion ships a templated Android
Auto car app that mirrors recording status and coordinated start/stop onto the
head unit and logs whatever vehicle data the head unit forwards
(`CarHardwareManager` → `phone/car_hw.jsonl`, every response logged including
`unavailable` — per-OEM availability is itself the measurement). The iOS
companion ships an interactive widget + Live Activity that appear on the
CarPlay Dashboard from iOS 26 (CarPlay forwards no vehicle data to apps), and
tags GPS fixes that were produced by the car rather than the phone
(`produced_by_accessory`). Design, platform limits, and the MBUX availability
spike: [`car-projection.md`](car-projection.md).

## Data flow contract

1. Edge and companion each record a session *part* keyed by a shared
   `session_id` (agreed via QR handshake or entered manually at drive start).
2. Parts are uploaded independently (WiFi/cellular when back in range). The
   server merges parts sharing a `session_id` into one session directory.
3. The server processes read-only inputs and writes only into `labels/` plus its
   own database and exported artifacts. Raw inputs are immutable — every
   derived result is reproducible from them.

## Repository layout

```
can-rosetta/
├── docs/           architecture, methodology, data format, roadmap
├── schemas/        JSON Schemas for every session file (normative, CI-checked)
├── edge/autopi/    in-vehicle app: discovery + logging (Python)
├── companion/      phone apps (ios/, android/): sensors + video + car projection
├── server/         alignment + identification + model (Python)
└── datasets/       tiny synthetic sample sessions for tests & demos
```

Each component has its own README with build/run instructions and its own tests.
The `schemas/` directory is the seam: change a schema, and the CI for all three
components re-validates against it.
