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

The brute-force sweep only enumerates the read services above. Any service that
could change vehicle state is out of scope for this project and PRs adding them
will be declined.

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
