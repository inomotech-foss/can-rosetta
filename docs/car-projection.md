# Car projection (Android Auto & CarPlay)

The companion app's job is to capture references while someone drives — but the
phone sits in a cradle, and the screen a driver may lawfully glance at is the
head unit's. Car projection puts the companion where the driver already looks.
On Android it does something more valuable still: it opens a **fourth reference
source** — whatever vehicle data the head unit itself forwards to apps — next to
GPS, IMU, and dashboard video.

## What ships

**Android Auto** — the Android companion carries a templated *car app*
(category `androidx.car.app.category.IOT`), which does two things:

1. **Status + coordinated start/stop on the head unit.** The car screen shows
   the recording state and offers the same coordinated start/stop as the phone
   UI, driving the *same* `RecordingController`/`EdgeConnection` — a thin
   template layer, no forked logic.
2. **Car-hardware reference logging.** While the car app is connected, it
   subscribes to every `CarHardwareManager` source — model, energy
   (battery %/fuel %/range/energy-low), speed (raw + display), mileage,
   car GNSS, accelerometer/gyroscope/compass — and appends one record per
   callback/fetch to **`phone/car_hw.jsonl`**
   ([schema](../schemas/car_hw.record.schema.json), documented in
   [`data-format.md`](data-format.md#phonecar_hwjsonl-optional)).
   **Every response is logged, including `unavailable`/`unimplemented`/`error`**
   — the per-OEM availability map is itself the deliverable (see the MBUX spike
   below), so a "failed" fetch is data, not noise.

**iOS** — there is no full CarPlay app yet (entitlement pending, see
[Distribution](#distribution-constraints)). What ships is the fast path that
requires **no entitlement**: an **interactive widget** (status + start/stop via
App Intents) and a **Live Activity** for the running recording — and from
**iOS 26** both appear on the **CarPlay Dashboard** as-is. In addition, iOS
tags every GPS fix with its provenance
([`produced_by_accessory`](#gps-provenance-produced_by_accessory)).

## Platform reality

The two projection platforms are *not* symmetric, and the differences dictate
the design:

|  | Android Auto | Apple CarPlay |
|---|---|---|
| Vehicle data for third-party apps | **Yes** — `CarHardwareManager`: model, energy, speed, mileage, car GNSS, accelerometer/gyroscope/compass. Each source is gated **twice**: by its own car permission (`com.google.android.gms.permission.CAR_INFO` / `CAR_FUEL` / `CAR_SPEED` / `CAR_MILEAGE`; fine location for car GNSS — the app manifest is the normative list) *and* by whatever the OEM head unit actually forwards | **None.** CarPlay exposes no vehicle-data API to third-party apps. The single vehicle-derived side channel is GNSS fused into CoreLocation by wireless-CarPlay head units (hence `produced_by_accessory`) |
| UI model | Templates only (list/grid/pane/message) — no custom canvas, **no charts** | Templates only — no custom canvas, **no charts** |
| Refresh cadence | ~1 s (host-throttled template updates) | ~10 s guidance for template refreshes |
| Availability while driving | IoT-category apps remain usable | Driving-task apps remain usable (content limits apply) |
| Getting onto the head unit | Templated apps **cannot be sideloaded** (Play-delivered only; the desktop head-unit emulator is the dev exception) | Requires a per-app CarPlay entitlement; Dashboard **widgets/Live Activities need none** from iOS 26 |

The "no charts" row is why neither platform shows live signal plots: the head
unit surface is for glanceable status and start/stop, nothing analytic.

## `phone/car_hw.jsonl`

The contract (normative:
[`car_hw.record.schema.json`](../schemas/car_hw.record.schema.json)):

- One JSON object per callback/fetch:
  `{"t_utc": …, "kind": …, "status": …, "data": {…}}`.
- `t_utc` is **phone-clock** Unix seconds — the same clock domain as
  `phone/motion.jsonl`, *not* the head unit's clock. The stamp is taken at
  callback delivery on the phone, so head-unit-side latency is absorbed into
  the server's usual alignment error budget.
- `status ∈ {success, unavailable, unimplemented, error}` and **non-success
  records are written on purpose** — deleting them would delete the answer to
  "does this OEM forward anything?".
- Values inside `data` are logged **verbatim** (no clamping, no range checks in
  the schema): an implausible value from a head unit is evidence.
- The stream is **optional**: it appears in `manifest.json` as a stream of
  kind `car_hw` only when a car session actually delivered records that drive.
  Sessions recorded without Android Auto are unchanged and stay valid.

When `status` is `success`, the records are labelled references in the usual
sense: `energy.battery_percent` is an SoC reference that needs no camera and no
OCR, `mileage.odometer_meters` a monotone anchor, `speed.*` a redundant speed
reference already on the phone clock.

## The MBUX spike (eVito)

The open question this feature exists to answer: **does the eVito's MBUX
forward anything through Android Auto's car-hardware API?** No public Mercedes
data point exists. The closest precedent is ABRP (A Better Routeplanner), which
ships exactly this kind of logger for EV state of charge — and their experience
is that OEM coverage is **spotty**: many head units answer `unavailable` for
everything beyond the model string.

Procedure, once per drive:

1. Connect the phone to the eVito's MBUX and **open the CAN-Rosetta car app on
   the head unit once** — the logger runs while the car-app session is
   connected.
2. Record the drive as usual.
3. Read the statuses out of the exported part:

   ```sh
   jq -r '[.kind, .status] | @tsv' phone/car_hw.jsonl | sort | uniq -c
   ```

4. Interpret:
   - `success` rows for `energy`/`speed`/`mileage` → an in-vehicle labelled
     reference on the phone clock, for free, every drive.
   - all-`unavailable`/`unimplemented` → the negative result, and it is equally
     valuable: it settles that SoC/odometer references must come from CAN/UDS
     discovery or the [cloud fallback](#cloud-fallback-oem-apis), and we stop
     wondering.

## GPS provenance: `produced_by_accessory`

Wireless CarPlay head units can feed the **vehicle's own GNSS** into
CoreLocation, and `CLLocationManager` then delivers those fixes transparently —
for navigation apps a feature, for us a trap: the phone's GPS is supposed to be
an *independent* reference against the bus, and a fix that actually originated
in the vehicle makes correlating "phone GPS speed" against a candidate CAN
signal partially circular.

So the iOS companion writes an optional boolean `produced_by_accessory` on
every `phone/location.jsonl` record (from
`CLLocation.sourceInformation.isProducedByAccessory`; omitted when iOS provides
no source information). Android never writes the field. The server can then
downgrade accessory-produced fixes: still useful for clock alignment, weaker as
independent identification evidence. Schema:
[`location.record.schema.json`](../schemas/location.record.schema.json).

## Distribution constraints

- **Android Auto:** templated car apps **cannot be sideloaded** onto a real
  head unit — the Android Auto host only surfaces Play-delivered apps (the
  desktop head-unit emulator accepts local builds for development). The
  practical path is the **Play internal-testing track**, which distributes to
  named testers *without* the car-app review that a production release
  requires.
- **CarPlay:** a full CarPlay app needs the
  `com.apple.developer.carplay-driving-task` entitlement. The application is
  **pending and user-owned** (Apple grants it to the developer account, not to
  this repo). Wording guidance for the application: describe the app as a
  **"fleet accessory controller"** — it controls a recording accessory (the
  AutoPi) and shows recorder status — and *not* as "real-time engine data",
  which is the phrasing that gets driving-task applications rejected. Until
  granted, the iOS 26 Dashboard widget/Live Activity path above is the shipping
  CarPlay presence.

## Cloud fallback: OEM APIs

Independent of projection entirely: the **Mercedes-Benz Fleet API** (or
aggregators such as **Smartcar** and **Enode**) exposes odometer and EV state
of charge from the vehicle's own backend at **minutes cadence**. That is far
too slow for waveform correlation, but it provides:

- sparse **anchors for slow signals** — the odometer's monotone ramp, SoC
  across a whole charge or drive — which is precisely what a
  [collinear charging session](methodology.md#charging) lacks, and
- an independent **cross-check** on values decoded from CAN.

If the MBUX spike comes back all-`unavailable`, this is the designated next
source of in-fleet SoC/odometer references.
