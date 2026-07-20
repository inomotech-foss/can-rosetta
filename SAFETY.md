# Safety & responsible use

CAN-Rosetta connects to a vehicle's diagnostic/CAN interface. That bus controls
real, safety-critical systems. Read this before you plug anything in.

## Authorization

Only use this on **vehicles you own or have explicit, documented permission to
test**. Reverse-engineering a vehicle bus and intercepting its traffic may be
regulated where you live. You are responsible for compliance.

## Read-only by design

The discovery tooling issues **only safe, read-style requests**:

- OBD-II service `0x01` (current data) and `0x09` (vehicle info).
- UDS `0x22` ReadDataByIdentifier and `0x19` ReadDTCInformation.
- Passive sniffing of broadcast traffic (no transmission at all).

It will **never**, by design and by default configuration:

- write anything: no `0x2E` WriteDataByIdentifier, no `0x31` RoutineControl,
  no `0x2F` InputOutputControl;
- enter non-default diagnostic sessions (`0x10` beyond default) or request
  security access (`0x27`);
- send ECU reset (`0x11`), communication control, or any programming-mode
  service;
- transmit on plain-CAN arbitration IDs used by vehicle ECUs.

The brute-force sweep only enumerates the read services above.

## Intrusive mode — opt-in, off by default

There is **one** capability that steps outside the read-only contract, and it is
**disabled unless you explicitly ask for it** with `--allow-session` (config
`allow_active_session: true`). It lives in its own module (`active.py`) behind its
own guard so the read-only core can never reach it by accident. When enabled it
may, on the ECUs that already answered read-only:

- open an **extended diagnostic session** (`0x10 0x03`) — never the programming
  session (`0x02`);
- hold it with **TesterPresent** (`0x3E`);
- re-issue the same **read** services (`0x22`/`0x19`) inside that session.

Even in this mode it still **never** does SecurityAccess (`0x27` — a wrong key
can lock an ECU out), writes, routines, I/O control, or resets. An extended
session can suppress an ECU's normal messaging, so **only ever use it with the
vehicle stationary.** Any service that could actuate the vehicle remains out of
scope and PRs adding them will be declined.

**Over-the-air updates are software-only.** The phone can update the AutoPi's edge
app (`POST /api/update`), but it installs **only** `canrosetta-edge` from the
official `inomotech-foss/can-rosetta` repo over HTTPS at a pinned `edge-v*` tag — a
non-official source is refused, and it can be disabled with
`allow_remote_update: false`. Updating the edge software has no bearing on the
read-only-vehicle guarantee; it never issues a vehicle service.

**Command identification is passive.** The server can *identify* which messages
are commands (by observing that a message's change precedes its effect — see
`roles.py`), and can *decode* their structure into a DBC. It does **not**, and
this project will not, transmit, replay, or inject command frames onto a vehicle
bus. Identifying command structure from recorded traffic is analysis; sending
commands is actuation, which is out of scope.

## Operational rules

- **Never run discovery or brute-force while the vehicle is moving.** Probe with
  the vehicle stationary, ideally with the engine off or idling in a safe place.
  Continuous *logging* (passive sniff) during a drive is fine — that's the point.
- Even read-only requests can, on some ECUs, cause unexpected behavior or set
  diagnostic trouble codes. Throttle aggressively; the defaults are conservative.
- Watch for a bus that becomes unresponsive and stop immediately if so.
- Keep a way to disconnect quickly.

## Data & privacy

- GPS traces and dashboard video are **personal data**. Sessions can reveal where
  you live, work, and drive. Store and share them accordingly.
- VINs are hashed, never stored raw, by the tooling. Don't defeat that.
- Don't upload sessions containing other people's data without consent.

## No warranty

Apache-2.0, Section 7–8: this software is provided "as is", without warranty, and
the authors are not liable for any damage to your vehicle, data, or anything else.
