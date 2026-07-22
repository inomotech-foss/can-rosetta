# Edge control protocol

The companion phone steers the AutoPi over a small **local HTTP + WebSocket API**
that the AutoPi serves on its own network (typically the AutoPi's WiFi access
point, to which the phone connects). It is **peer-to-peer and offline** — no
internet and no external server are involved, consistent with the rest of the
system. The AutoPi is the server; the phone is the client. How the phone gets
onto that network in the first place — the pairing payload carries the AP
credentials and the app joins programmatically — is covered in
[connection.md](connection.md).

This lets the driver, from the phone, start an investigation, pick the discovery
mode (fast / brute-force), and start/stop recording — and it solves two things
the offline design otherwise left manual: agreeing a shared `session_id`, and
pinning the two device clocks together.

Base URL: `http://<autopi-host>:8765` (host/port configurable). All request and
response bodies are JSON.

## Authentication

Every request must carry `Authorization: Bearer <token>`, where the token is the
pre-shared `control_token` from the AutoPi's config (entered once in the phone
app when pairing). If the AutoPi is configured with an empty token, auth is
disabled (development only) and it logs a warning. Requests without a valid token
get `401`.

## Chirps

The edge plays two short, **best-effort** speaker chirps (tones at 1–4 kHz, the
speaker's sweet spot; disable with `chirp: false`): one when `serve` starts —
the AutoPi wakes on ignition, so this is the driver's "logger is ready" cue —
and one when the **first authenticated client request** arrives, confirming the
phone got through WiFi join and auth. A missing speaker never blocks anything.
Details in [connection.md](connection.md#chirps).

## State machine

The edge is always in exactly one state:

```
 idle ──POST /api/discover──► discovering ──(done)──► idle
   │                                                    │
   ├──POST /api/log/start────► logging ──POST /api/log/stop──► idle
   │                                                    │
   └──POST /api/run {mode}───► discovering ──► logging ──POST /api/log/stop──► idle
```

Only one job runs at a time. Starting a job while busy returns `409 Conflict`.
`error` is a terminal-ish state that clears on the next successful command.

## Endpoints

### `GET /api/health`
Unauthenticated liveness check. `{ "ok": true, "sw_version": "..." }`.

### `GET /api/time`
Server clock, for a Cristian's-algorithm time sync. Returns
`{ "t_utc": 1752624000.123 }`. The phone records send-time `t0` and receive-time
`t1`, and estimates the AutoPi clock at `t1` as `t_utc + (t1 - t0)/2`, giving the
offset `edge_utc - companion_utc`. Call it a few times and keep the sample with
the smallest round-trip. Pass the result into `POST /api/session` so the edge
records it in its manifest (see [time sync](#time-sync) below).

### `GET /api/status`
Current snapshot:

```jsonc
{
  "state": "logging",
  "session_id": "9f2c...",
  "output_dir": "/home/pi/sessions/9f2c...",
  "device": { "id": "autopi-abcd", "sw_version": "can-rosetta-edge/0.1.0" },
  "mode": "fast",                       // last discovery mode, if any
  "stats": { "elapsed_s": 42.1, "frames": 84120, "obd_samples": 210 },
  "discovery_summary": { "obd_pids": 12, "uds_dids": 1, "plain_can_ids": 37 },
  "error": null
}
```

### `POST /api/session`
Create/point-to a session before discovering or logging.

```jsonc
// request (all optional)
{
  "session_id": "9f2c...",              // omit to have the edge mint one
  "vehicle": { "make": "VW", "model": "Golf", "year": 2019 },
  "edge_utc_offset_est_s": 0.031,       // measured edge_utc - companion_utc
  "clock_source": "gps"                 // the companion's clock source
}
// response
{ "session_id": "9f2c...", "output_dir": "/home/pi/sessions/9f2c...",
  "device": { "id": "autopi-abcd", "sw_version": "..." } }
```

The phone and AutoPi **must share the same `session_id`** so the server can merge
their parts. Recommended flow: the phone mints the id (it also names its own
session part) and sends it here.

### `POST /api/discover`
`{ "mode": "fast" | "slow" }` → `202` `{ "state": "discovering" }`. Runs Stage 1a
(fast catalog scan or slow brute-force). Progress and completion arrive on the
WebSocket. `mode` is required.

### `POST /api/log/start`
Begin continuous CAN logging (Stage 1b). If a discovery result exists, its
supported PIDs are polled during logging to build the reference series.
→ `202 { "state": "logging" }`.

### `POST /api/log/stop`
Stop the running job (discovery or logging), flush, and finalize the manifest.
→ `200 { "state": "idle", "frames": 84120 }`.

### `POST /api/run`
`{ "mode": "fast" | "slow", "duration_s": null }` → discover, then immediately
start logging. `duration_s` null means log until `POST /api/log/stop`.

### `GET /api/discovery`
Returns the current `discovery.json` contents (or `404` if none yet).

### `GET /api/version`
The installed edge version, and (with `?check=1`, a best-effort network call) the
latest `edge-v*` release: `{ "current": "0.1.0", "latest": "0.3.0",
"update_available": true, "repo": "inomotech-foss/can-rosetta" }`. Backs the
phone's "Update AutoPi" affordance (see [provisioning.md](provisioning.md)).

### `POST /api/update`
`{ "target": "edge-v0.3.0" }` (omit for latest) → pip-installs that release of
`canrosetta-edge` from the **official repo** over HTTPS and re-execs into it.
Returns `{ "from", "to", "restarting": true }`. Refuses a non-official source, a
disabled `allow_remote_update`, or running mid-recording (`409`). Read-only w.r.t.
the vehicle — it updates the edge *software* only.

### `GET /api/ws`  (WebSocket)
After the HTTP upgrade (same bearer token via the `Authorization` header or a
`?token=` query param), the server streams newline events as JSON:

```jsonc
{ "event": "state",     "state": "discovering", "ts": 1752624001.0 }
{ "event": "discovery", "phase": "obd", "supported_pids": 12, "ts": ... }
{ "event": "discovery_done", "summary": { "obd_pids": 12, ... }, "ts": ... }
{ "event": "stats",     "frames": 12000, "obd_samples": 30, "elapsed_s": 6.0 }
{ "event": "state",     "state": "idle", "ts": ... }
{ "event": "error",     "message": "…", "ts": ... }
```

The client should also `GET /api/status` on connect for the initial snapshot,
then apply events.

## Time sync

The control channel is the opportunity to align the clocks *before* the drive,
which shrinks the residual offset the server must recover by cross-correlation:

1. Phone calls `GET /api/time` a few times (Cristian's algorithm) → estimates
   `edge_utc_offset_est_s = edge_utc − companion_utc`.
2. Phone passes that into `POST /api/session`.
3. The edge writes it into its manifest `devices[edge].clock.utc_offset_est_s`
   (semantics: *device_clock − UTC*, treating the phone/GPS as UTC truth), which
   the server's aligner uses as its prior (see
   [methodology](methodology.md#stage-2-time-alignment)). Cross-correlation then
   only has to clean up the residual.

## Safety

The control API exposes **only** the read-only discovery and passive-logging
capabilities documented in [SAFETY.md](../SAFETY.md). There is no endpoint that
can issue a vehicle write, actuator, or session/security-access service — the
same service-id guards apply underneath. Bind the server to the AutoPi's local AP
interface, not a public network.
