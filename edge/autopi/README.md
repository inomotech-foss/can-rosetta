# CAN-Rosetta — edge (in-vehicle) component

The **edge** component runs on the vehicle-connected device (an
[AutoPi](https://www.autopi.io/), a Raspberry-Pi-based OBD/CAN dongle) and does
the two in-vehicle stages of the CAN-Rosetta pipeline:

- **Stage 1a — Discovery.** Enumerate what the vehicle answers: OBD-II
  supported-PID bitmasks (and their live values), the standard UDS
  ReadDataByIdentifier catalog per ECU, and a passive plain-CAN census. Written
  to `can/discovery.json`.
- **Stage 1b — Continuous logging.** Sniff **every** frame on the bus into
  `can/frames.parquet` (the haystack), while polling the discovered OBD/UDS
  signals at a steady rate to build a dense, labelled reference series.

It also:

- **Logs the AutoPi's own IMU/GPS** (`edge/motion.jsonl`, `edge/location.jsonl`)
  beside the CAN bus — on the *same clock* as the frames, so these are the
  server's most reliable motion references (no cross-device alignment needed).
- **Serves a local control API** so the companion phone can create a shared
  session, choose the discovery mode, and start/stop recording remotely — offline,
  over the AutoPi's own WiFi (see [control-protocol.md](../../docs/control-protocol.md)).
- **Holds a wake lock** while discovering/logging so the AutoPi never sleeps
  mid-recording.
- **Self-provisions and updates from the phone**: bootstrap once
  (`scripts/bootstrap.sh`), then the phone updates the edge app over the control
  link (`POST /api/update`, official source only). See
  [docs/provisioning.md](../../docs/provisioning.md).

The output is a **session part** in the shared
[data format](../../docs/data-format.md); the CAN-Rosetta server merges it with
the phone companion's part and does alignment + signal identification.

The same code runs on a dev laptop with a USB-CAN adapter, or with no hardware
at all against a built-in simulated bus.

## Safety — read-only by design

This tool issues **only safe, read-style requests**: OBD services `0x01`
(current data) and `0x09` (vehicle info), and UDS services `0x22`
(ReadDataByIdentifier) and `0x19` (ReadDTCInformation), plus passive sniffing.
It never writes, never enters non-default diagnostic sessions, never requests
security access, and never resets an ECU. Every OBD/UDS request passes through a
service-id guard (`assert_read_only_mode` / `assert_read_only_service`) that
raises on anything else. See [SAFETY.md](../../SAFETY.md) — and **never run
discovery/brute-force while the vehicle is moving.**

## Install

```bash
cd edge/autopi
pip install -e .                 # core (pyyaml only)
pip install -e ".[dev]"          # + pytest, jsonschema, ruff (to run tests)
```

Optional hardware/format extras (imported lazily — not needed for tests):

```bash
pip install -e ".[can]"          # python-can  → SocketCAN transport
pip install -e ".[elm]"          # pyserial    → ELM327/STN serial transport
pip install -e ".[parquet]"      # pyarrow     → Parquet frame log
```

### On the AutoPi

The AutoPi runs Python natively with a SocketCAN interface (typically `can0`).

```bash
pip install -e ".[can,parquet]"
canrosetta-edge run --transport socketcan --channel can0 --output-dir /data/session-<id>
```

If `pyarrow` is unavailable the logger transparently falls back to a
line-appended `can/frames.jsonl` with identical per-row semantics.

## Transports

| Transport                  | Backend            | Use |
|----------------------------|--------------------|-----|
| `NativeSocketCanTransport` | **stdlib only**    | AutoPi / any Linux SocketCAN device — **no `pip` needed** |
| `SocketCanTransport`       | `python-can`       | AutoPi / any Linux SocketCAN device |
| `ElmTransport`             | `pyserial`         | ELM327 / STN serial dongles (best-effort sniffing) |
| `SimulatedTransport`       | none               | tests + `simulate` demo; a fake vehicle bus |

All implement one interface (`transport.py`): `send_frame`, `recv_frames`, and
an ISO-TP style `request(tx_id, rx_id, payload)` for OBD/UDS request-response.
The native and python-can SocketCAN transports also expose `request_all()`,
which broadcasts a **functional** request and collects *every* ECU's response in
one shot (used for multi-ECU discovery), and handle both 11-bit and 29-bit
(`0x18DAxxF1`) diagnostic addressing.

For `--transport socketcan`, the backend is picked by
`config.socketcan_backend` (`auto` | `python-can` | `native`). The default
`auto` uses `python-can` when it is importable and otherwise falls back to the
**native stdlib** transport — so the edge runs on a stock AutoPi that has no
`pip`.

## Reverse-engineering recon (first contact with an unknown bus)

The `recon` flow answers the three first-contact questions in one pass and is
the fastest way to characterise a vehicle you know nothing about:

1. **What is the CAN speed?** — passive, listen-only bitrate detection across the
   AutoPi's CAN interfaces (never transmits while probing).
2. **What plain-CAN messages exist?** — a passive census of every arbitration id
   (period, width, changing bytes, ASCII preview).
3. **Which OBD/UDS signals are readable?** — the catalog scan over **both**
   11-bit and 29-bit addressing, enumerating every responding ECU.

It also reads **stored DTCs** (`0x19`, read-only) from each responding ECU and
decodes them to SAE codes.

```bash
# via the CLI (writes can/discovery.json under --output-dir)
canrosetta-edge recon --output-dir ./session          # auto-detect bus + speed
canrosetta-edge recon --interface can0 --bitrate 500000
canrosetta-edge recon --deep                          # + brute-force PIDs/DIDs (throttled)

# or the zero-install standalone script (nothing but Python 3 + SocketCAN)
python3 reverse_engineer.py                            # auto-detect, fast scan
python3 reverse_engineer.py --deep --json out.json
```

**Intrusive mode (opt-in, stationary vehicle only).** `--allow-session` steps
outside the read-only contract to open a UDS **extended session** (`0x10 0x03`,
held with TesterPresent) on the ECUs that answered, then re-reads DIDs to see
what the session unlocks. It never does SecurityAccess/writes/routines/resets.
See [SAFETY.md](../../SAFETY.md).

```bash
python3 reverse_engineer.py --allow-session           # extended-session probe
```

Bitrate probing reconfigures the controller via `ip link`, which needs root
(`sudo`); an interface that is already up is sampled **as-is** and never touched.
An EV that answers no OBD/UDS is normal — the value is then in the plain-CAN
census, correlated on the server against the phone/IMU/GPS reference series.

Discovery probes **functional broadcast and a physical ECU sweep**, because some
vehicles (Mercedes-Benz included) answer diagnostics *only* when addressed
physically (`0x7E0..0x7E7` / `0x18DA{ECU}F1`) and ignore the functional request.
All diagnostic frames are padded to 8 bytes (ISO 15765-4) — gateways commonly
drop short frames.

### Why a hand-rolled ISO-TP instead of `udsoncan`/`can-isotp`?

The established stack (`python-can` + `can-isotp` + `udsoncan`, the `[uds]`
extra) is the more battle-tested choice and is **preferred when it is
installed**. But the edge's baseline target is a locked-down AutoPi where `pip`
is unavailable and the kernel has no `can_isotp` module, so it must also run with
**nothing but the standard library** — hence `NativeSocketCanTransport` and its
small ISO-TP implementation (single-frame requests + multi-frame reassembly,
which covers every read service we issue). Its output has been cross-checked
against `udsoncan` on real hardware (identical VIN read). Install the `[uds]`
extra to use the established stack where you can.

## Usage

```bash
# Stage 1a only — write can/discovery.json (+ manifest.json)
canrosetta-edge discover --transport socketcan --mode fast  --output-dir ./session
canrosetta-edge discover --transport socketcan --mode slow  --output-dir ./session

# Stage 1b only — continuous capture (Ctrl-C to stop, or --duration)
canrosetta-edge log --transport socketcan --duration 600 --output-dir ./session

# Normal in-vehicle flow — discover, then log
canrosetta-edge run --transport socketcan --mode fast --duration 600 --output-dir ./session

# No hardware — end-to-end demo against the simulated bus
canrosetta-edge simulate --output-dir /tmp/demo-session

# Run the control server so the phone can steer this device (needs the [control] extra)
canrosetta-edge serve --transport socketcan --control-port 8765 --control-token "$TOKEN"

# Headless setup: print the pairing host/token + a scannable terminal QR
canrosetta-edge pairing --control-token "$TOKEN"
```

Since the AutoPi is headless, `serve` prints the pairing block (host, token, and
an ASCII QR you can scan straight off the SSH terminal) on startup; `pairing`
prints it on demand. In the app, scan it or enter host + token manually.

All of `log`/`run`/`simulate`/`serve` also log the AutoPi's onboard sensors and
hold the wake lock. The control server (`serve`) is documented in
[control-protocol.md](../../docs/control-protocol.md); install it with:

```bash
pip install -e ".[control]"      # aiohttp → local HTTP + WebSocket control API
```

`--session-id` sets the shared id agreed with the phone (a fresh UUID is minted
otherwise). Configuration defaults live in `config.py` and can be overridden
with `--config path/to/config.yaml`, e.g.:

```yaml
transport: socketcan
channel: can0
bitrate: 500000
poll_rate_hz: 5.0
brute_force_throttle_s: 0.05
uds_did_min: 0xF100
uds_did_max: 0xF1FF
plain_can_census_s: 10.0
```

## Session output layout

```
session-<id>/
├── manifest.json          # edge device + stream index (manifest.schema.json)
├── can/
│   ├── frames.parquet     # or frames.jsonl fallback (can_frame.record.schema.json)
│   └── discovery.json     # discovery.schema.json
└── edge/                  # the AutoPi's onboard sensors (edge clock)
    ├── motion.jsonl       # IMU (motion.record.schema.json)
    └── location.jsonl     # GPS (location.record.schema.json)
```

## Tests

Tests use **only** `SimulatedTransport` and the JSONL fallback, so no hardware
and none of the optional extras are required:

```bash
cd edge/autopi
pip install -e ".[dev]"
python -m pytest -q
```

They verify that (a) discovery finds the simulated PIDs + VIN and validates
against `discovery.schema.json`, (b) the logger captures the periodic frames and
each row validates against `can_frame.record.schema.json`, (c) the manifest
validates against `manifest.schema.json`, and (d) the read-only guards reject
non-read services.
