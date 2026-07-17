"""EV-specific reverse-engineering: references, priors, and a signal registry.

Electric vehicles put a distinctive family of signals on the bus that combustion
cars don't — high-voltage **battery** pack voltage/current, **state of charge**,
**cell** voltages/temperatures, **motor** speed/torque, and **regenerative
braking**. This module adds the domain knowledge to find them:

- *derived references* from the sensors we already have (a regen-braking event,
  a tractive-power proxy) so EV signals have something to correlate against even
  when no EV-specific OBD PID is readable;
- *physical priors* unique to EVs (battery power = V·I; SoC integrates current;
  regen makes current/torque go negative under deceleration);
- a *name registry* of EV signal types so the identifier and DBC export can label
  them.

Everything here is passive analysis of recorded data.
"""

from __future__ import annotations

import numpy as np

from .session import Session, TimeSeries

# Canonical EV signal names the identifier/DBC use.
EV_SIGNALS = (
    "hv_battery_voltage",
    "hv_battery_current",  # signed: + discharge (drive), - charge (regen/plug)
    "hv_battery_soc",
    "hv_battery_power",
    "cell_voltage",
    "cell_temp",
    "motor_rpm",
    "motor_torque",  # signed like current
    "regen_level",
    "charge_state",
)


def ev_references(session: Session) -> list[TimeSeries]:
    """EV-relevant references derived from base sensors + any EV OBD samples."""
    refs: list[TimeSeries] = []

    # Regen braking = deceleration that isn't the friction brake. We approximate
    # it from signed longitudinal accel: strong negative accel marks braking, and
    # on an EV most gentle deceleration is regen. This is an *event* reference the
    # battery-current / motor-torque sign should track.
    for src, clock in (("motion", "companion"), ("edge_motion", "edge")):
        m = getattr(session, src)
        if m and "acc_x" in m:
            t = m["t_utc"]
            decel = (-m["acc_x"]).clip(min=0.0)  # magnitude of deceleration, g
            name = "regen_brake" if src == "motion" else "edge_regen_brake"
            refs.append(TimeSeries(name, t, decel, unit="g", clock=clock))

    # Tractive-power proxy: signed accel × speed ~ power delivered/recovered.
    loc = session.location
    if loc and session.motion and "speed" in loc:
        # resample-free proxy: use GPS speed sampled onto motion times is overkill
        # here; the identifier does the aligning. Provide accel×|speedtrend|.
        pass  # power proxy is left to the identifier via battery power = V*I prior

    # State of charge / battery current if an EV OBD/UDS sample exposed them.
    refs += _ev_obd_references(session)
    return [r for r in refs if len(r.t) >= 8]


def _ev_obd_references(session: Session) -> list[TimeSeries]:
    obd = session.discovery.get("obd", {})
    wanted = {
        "hybrid_battery_remaining": "hv_battery_soc",
        "ev_battery_soc": "hv_battery_soc",
        "soc": "hv_battery_soc",
        "hv_battery_current": "hv_battery_current",
        "hv_battery_voltage": "hv_battery_voltage",
    }
    by_name: dict[str, list[tuple[float, float]]] = {}
    for s in obd.get("samples", []):
        canon = wanted.get((s.get("name") or "").lower())
        v = s.get("value")
        if canon and isinstance(v, (int, float)):
            by_name.setdefault(f"ev_{canon}", []).append((float(s["t_utc"]), float(v)))
    out: list[TimeSeries] = []
    for nm, pts in by_name.items():
        pts.sort()
        out.append(TimeSeries(nm, np.array([p[0] for p in pts]),
                              np.array([p[1] for p in pts]), clock="edge"))
    return out


def soc_from_current(t: np.ndarray, current: np.ndarray, capacity_ah: float,
                     soc0: float = 100.0) -> np.ndarray:
    """Integrate battery current into a State-of-Charge curve (Coulomb counting).

    Used as a physical-consistency check: a candidate SoC signal must match the
    integral of a candidate current signal. ``current`` is amps (+ discharge).
    """
    t = np.asarray(t, dtype=np.float64)
    charge_removed_ah = np.concatenate([[0.0], np.cumsum(
        np.asarray(current[:-1], dtype=np.float64) * np.diff(t) / 3600.0)])
    return soc0 - 100.0 * charge_removed_ah / capacity_ah


def battery_power(voltage: np.ndarray, current: np.ndarray) -> np.ndarray:
    """P = V·I (watts). The defining EV relationship linking three candidates."""
    return np.asarray(voltage, dtype=np.float64) * np.asarray(current, dtype=np.float64)
