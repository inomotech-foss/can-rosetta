# Connection establishment (phone ⇄ AutoPi)

The [control protocol](control-protocol.md) documents what the phone can *say*
to the edge; this page documents how the phone **gets onto the AutoPi's network
in the first place**, and how the driver knows the edge is ready when neither
device has a screen anywhere near the OBD port. The goal is near-zero friction:
after a one-time QR scan, every subsequent drive should need **zero taps** —
open the app, hear the chirp, record. Token generation and updates are covered
in [provisioning.md](provisioning.md).

## The network is already there

The AutoPi TMU (a CM4-based device) runs a **WPA2 WiFi access point whenever it
is powered**: SSID `AutoPi-<last 12 chars of the unit id>`, always
`192.168.4.1` on interface `uap0`. There is nothing to enable and no mode to
switch into — the whole connection problem reduces to *getting the phone to
join that AP without a trip to Settings*.

The factory AP passphrase is the **first 13 characters of the AutoPi Device
ID**. Rotate it: the Device ID also gates AutoPi's own local API, so leaving
the factory default means every pairing QR effectively prints a device
credential.

## End-to-end flow

```
vehicle            AutoPi                                        phone
─────────          ──────────────────────────────                ─────────────────────────────
ignition on ─────► supply voltage rises
                   (Smart Power Manager wake trigger,
                    default +0.50 V over 1000 ms)
                   boot → systemd starts
                   `canrosetta-edge serve` ──► READY CHIRP
                                                                 app opened
                                                                 [first time only: scan the
                                                                  pairing QR — v2 payload with
                                                                  the AP WiFi credentials]
                                                                 programmatic WiFi join
                   ◄──────────────────────────────────────────── GET /api/health   (link up?)
                   ◄──────────────────────────────────────────── GET /api/time ×N  (Cristian sync)
                   first authenticated request ──► CONNECTED CHIRP
                                                                 create session, record
```

The AutoPi wakes on the ignition voltage rise and systemd starts `serve`
immediately, so **`serve` startup *is* ignition-ready** — the ready chirp the
driver hears is the ignition trigger made audible. The connected chirp confirms
the phone made it through join + auth (the first bearer-token request, which in
the normal flow is the first `GET /api/time` of the clock sync).

## Pairing payload v2

The QR/JSON pairing payload is extended — backwards-compatibly — to carry the
AP credentials (normative schema:
[`pairing_payload.schema.json`](../schemas/pairing_payload.schema.json)):

```json
{ "host": "http://192.168.4.1:8765", "token": "<control_token>",
  "wifi": { "ssid": "AutoPi-<last12>", "psk": "<ap passphrase>" } }
```

The `wifi` key is **omitted when the credentials are unknown** (dev boxes with
no AP). Consumers must tolerate its absence and behave exactly as before: the
user joins the network manually, then pairs with host + token.

## Programmatic join

- **Android** joins via `WifiNetworkSpecifier`: **one system dialog on the
  first join**, then silent auto-join for identical requests. The network is
  app-scoped and local-only — on Android 12+ the phone keeps its internet on
  cellular even while on the AP, so the control link never fights the user's
  connectivity.
- **iOS** joins via `NEHotspotConfiguration`: **one join alert on the first
  apply**, after which the configuration is persisted; plus iOS's **one-time
  Local Network prompt** for the control traffic itself.

### Friction, per platform

| Platform | First session (one-time)                                                   | Every session after |
|----------|----------------------------------------------------------------------------|---------------------|
| Android  | scan QR + accept one system join dialog                                    | zero taps — silent auto-join |
| iOS      | scan QR + accept one join alert + the one-time Local Network prompt        | zero taps while in use; one tap to reconnect if iOS dropped the AP |

### Reconnect semantics

iOS drops a no-internet AP when the device locks and will not auto-rejoin it,
so the app offers a one-tap **reconnect** (re-applying the persisted
`NEHotspotConfiguration`). This costs nothing but the tap: the edge records to
its own disk regardless of the control link, and the session parts are merged
server-side afterward — a dropped link never loses data (see
[architecture.md](architecture.md)).

## Firewall: the control port on `uap0`

The AutoPi's stock hotspot firewall **default-drops all inbound traffic on
`uap0` except ports 22/53/67/80**, which would silently break the control port.
The systemd unit installed by `bootstrap.sh` therefore opens the port itself
with an idempotent `ExecStartPre` iptables rule on **every service start**, so
the permit survives reboots and edge updates. An AutoPi Cloud configuration
re-sync can still rewrite the firewall while the service runs; for a rule that
survives those too, add the same permit under **AutoPi Cloud → Advanced
Settings**.

## Chirps

The edge plays two short speaker chirps, both **best-effort** — a missing or
broken speaker never blocks recording:

- **Ready chirp** when `serve` starts. Because the device wakes on ignition,
  this is the driver's "the logger saw the ignition" cue.
- **Connected chirp** on the **first authenticated client request**, confirming
  the phone got through WiFi join and auth.

Tones sit in **1–4 kHz**, the sweet spot of the AutoPi's small speaker.
Disable with `chirp: false` in the edge config.

## Design rationale — alternatives we rejected

We evaluated four bootstrap channels before settling on QR-carried AP
credentials + programmatic join:

- **BLE bootstrap (the GoPro pattern)** — rejected. The CM4's WiFi and
  Bluetooth share one 2.4 GHz radio, and the combo firmware has a documented,
  unfixed coexistence defect: **BLE connection attempts fail while the 2.4 GHz
  AP is active** (RPi-Distro/bluez-firmware issue #13). And since the AP is
  already always-on, a BLE side-channel would add a failure mode without
  removing a single step.
- **Acoustic credential transfer (data-over-chirp)** — rejected. The device has
  no microphone, so the channel is one-way with no ACK; a 40–80 byte credential
  payload is **3–8 s of audible chirping** at robust rates; and near-ultrasonic
  transfer is unproven on a voice-grade speaker. The speaker is kept for what
  it is good at: a one-bit readiness cue.
- **Phone-as-hotspot (reversing the roles)** — rejected. iOS has no API to
  enable the personal hotspot programmatically and hides its SSID when idle;
  Android's `LocalOnlyHotspot` generates **random credentials on every start**,
  which would itself need a side channel to communicate.
- **Bluetooth Classic (SPP)** — rejected: iOS requires MFi certification for
  Classic accessories.

**Bandwidth grounding** for why WiFi stays the data plane: a charging eVito
averaged ~1,425 frames/s (~1.7 Mbit/s as JSONL) — trivial for WiFi, beyond
what BLE reliably delivers on this radio (~10–50 KB/s).
