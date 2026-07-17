"""EV charging reverse-engineering: connector state, AC/DC mode, AC metering.

Charging is a different regime from driving: the car is parked and plugged in, so
the driving references (GPS/IMU) are flat. Charging signals are grounded instead
by:

- the **charge-state timeline** — connector plugged / locked / charging /
  complete — as event references (from a charge telltale or the car's charge
  screen via perception);
- the **EVSE/charger display** OCR'd through the perception module
  (`dash_ac_voltage`, `dash_ac_current`, `dash_charge_power`, `dash_charge_state`);
- **rising SoC** (OBD PID 0x5B or a candidate) during a charge;
- **physics**: AC power = phases · V · I · pf; DC power = V · I; and both equal the
  pack power V_pack · I_pack (minus losses), which links the AC-side and
  DC-side candidates.

Those references flow through the normal identifier; this module adds the charging
domain vocabulary, an AC/DC discriminator, and the power relationships.
"""

from __future__ import annotations

import numpy as np

# connector / charge state machine (typical ordering)
CHARGE_STATES = ("idle", "connected", "locked", "charging", "complete", "fault")

# charge modes
AC = "ac"
DC = "dc"

# charging-related canonical signal names
CHARGING_SIGNALS = (
    "charge_state",
    "connector_locked",
    "charging_active",
    "charge_mode",  # AC vs DC
    "ac_phase_count",  # 1 or 3
    "ac_voltage",  # per-phase RMS volts
    "ac_current",  # per-phase RMS amps
    "charge_power",  # watts
    "dc_charge_current",
    "dc_charge_voltage",
)


def ac_power(voltage: np.ndarray, current: np.ndarray, phases: int = 3,
             power_factor: float = 1.0) -> np.ndarray:
    """AC charging power (watts): ``phases · V · I · pf`` (per-phase V/I RMS).

    For a 3-phase supply with line-to-neutral voltage this is the standard
    3·Vph·Iph·pf; single-phase is phases=1. Used to cross-check an AC-metering
    candidate triple against a DC-side power candidate.
    """
    v = np.asarray(voltage, dtype=np.float64)
    i = np.asarray(current, dtype=np.float64)
    return phases * v * i * power_factor


def classify_mode(ac_current: np.ndarray | None, dc_current: np.ndarray | None,
                  *, dc_amp_threshold: float = 50.0) -> str:
    """Decide AC vs DC charging from which current channel is active.

    AC charging drives the onboard-charger AC current; DC fast charging bypasses
    it and pushes large current straight to the pack. If a candidate AC current is
    materially non-zero it's AC; else a large DC current means DC.
    """
    if ac_current is not None and np.nanmax(np.abs(ac_current)) > 1.0:
        return AC
    if dc_current is not None and np.nanmax(np.abs(dc_current)) > dc_amp_threshold:
        return DC
    return AC


def infer_phase_count(phase_currents: list[np.ndarray], *, active_amps: float = 1.0) -> int:
    """Count how many AC phases carry current (1 for single-phase, 3 for three)."""
    return sum(1 for pc in phase_currents if np.nanmax(np.abs(pc)) > active_amps)


def charge_state_name(code: int) -> str:
    return CHARGE_STATES[code] if 0 <= code < len(CHARGE_STATES) else "unknown"
