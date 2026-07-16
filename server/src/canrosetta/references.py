"""Build reference signals — the *known* half of the Rosetta stone.

References come from three places, in rough order of how directly they're
labelled:

1. OBD/UDS samples in ``discovery.json`` — already decoded and named. The
   strongest references (a candidate matching OBD speed almost certainly *is* the
   plain-CAN speed broadcast).
2. GPS — ground speed and its derivative (longitudinal acceleration).
3. IMU — acceleration and rotation, in the phone frame.

Every reference is a :class:`~canrosetta.session.TimeSeries` on the companion's
UTC clock. Alignment (see ``align.py``) has already been applied to the edge
clock before candidates are compared against these.
"""

from __future__ import annotations

import numpy as np

from .session import Session, TimeSeries
from .signals import derivative


def build_references(session: Session) -> list[TimeSeries]:
    refs: list[TimeSeries] = []
    refs += _gps_references(session)
    refs += _imu_references(session)
    refs += _obd_references(session)
    return [r for r in refs if len(r.t) >= 8]


def _gps_references(session: Session) -> list[TimeSeries]:
    loc = session.location
    if not loc:
        return []
    t = loc["t_utc"]
    out: list[TimeSeries] = []

    speed = loc.get("speed")
    if speed is not None and np.any(speed >= 0):
        s = speed.copy()
        s[s < 0] = np.nan
        out.append(TimeSeries("gps_speed", t, s, unit="m/s"))
        out.append(TimeSeries("gps_speed_kmh", t, s * 3.6, unit="km/h"))
        out.append(TimeSeries("gps_accel_long", t, derivative(t, s), unit="m/s^2"))
    return out


def _imu_references(session: Session) -> list[TimeSeries]:
    m = session.motion
    if not m:
        return []
    t = m["t_utc"]
    acc = np.vstack([m["acc_x"], m["acc_y"], m["acc_z"]])  # g, gravity removed
    mag = np.linalg.norm(acc, axis=0)
    yaw_rate = m["rot_z"]  # rad/s about vertical ~ turn rate
    return [
        TimeSeries("imu_accel_mag", t, mag, unit="g"),
        TimeSeries("imu_yaw_rate", t, yaw_rate, unit="rad/s"),
    ]


def _obd_references(session: Session) -> list[TimeSeries]:
    obd = session.discovery.get("obd", {})
    samples = obd.get("samples", [])
    by_name: dict[str, list[tuple[float, float]]] = {}
    for s in samples:
        val = s.get("value")
        if val is None or not isinstance(val, (int, float)):
            continue
        name = s.get("name") or f"pid_{s.get('pid', '?')}"
        by_name.setdefault(f"obd_{name}", []).append((float(s["t_utc"]), float(val)))

    out: list[TimeSeries] = []
    for name, pts in by_name.items():
        pts.sort()
        t = np.array([p[0] for p in pts])
        v = np.array([p[1] for p in pts])
        unit = _first_unit(samples, name)
        # OBD/UDS samples are logged by the edge device, so they live on the edge
        # clock (like the CAN frames) and must be shifted by the alignment delta,
        # unlike the phone-sourced GPS/IMU references.
        out.append(TimeSeries(name, t, v, unit=unit, clock="edge"))
    return out


def _first_unit(samples: list[dict], ref_name: str) -> str:
    target = ref_name.removeprefix("obd_")
    for s in samples:
        if (s.get("name") or "") == target:
            return s.get("unit", "")
    return ""
