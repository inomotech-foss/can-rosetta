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
    # The AutoPi's onboard sensors are on the edge clock — no cross-device
    # alignment error — which makes them the most reliable motion references.
    refs += _edge_imu_references(session)
    refs += _edge_gps_references(session)
    # Dashboard-video-derived references reach signals no OBD PID exposes.
    refs += _video_label_references(session)
    return [r for r in refs if len(r.t) >= 8]


def _video_label_references(session: Session) -> list[TimeSeries]:
    """References read off the filmed dashboard (companion clock).

    Digits/needles become continuous references; telltales become *event*
    references (0/1) that should coincide with a CAN bit flip; gear becomes a
    (discrete-valued) continuous reference matched against an enum CAN field.
    """
    out: list[TimeSeries] = []
    vl = session.video_labels
    if not vl:
        return out

    for row_key, name_key, val_key, kind in (
        ("dashboard", "field", "value", "continuous"),
    ):
        by_name: dict[str, list[tuple[float, float]]] = {}
        for r in vl.get(row_key, []):
            v = r.get(val_key)
            if isinstance(v, (int, float)):
                by_name.setdefault(f"dash_{r.get(name_key, '?')}", []).append(
                    (float(r["t_utc"]), float(v)))
        for nm, pts in by_name.items():
            pts.sort()
            out.append(TimeSeries(nm, np.array([p[0] for p in pts]),
                                  np.array([p[1] for p in pts]), kind=kind))

    # telltales: one event reference per lamp name
    tt: dict[str, list[tuple[float, float]]] = {}
    for r in vl.get("telltales", []):
        tt.setdefault(f"telltale_{r.get('name', '?')}", []).append(
            (float(r["t_utc"]), float(r.get("state", 0))))
    for nm, pts in tt.items():
        pts.sort()
        out.append(TimeSeries(nm, np.array([p[0] for p in pts]),
                              np.array([p[1] for p in pts]), kind="event"))

    # gear
    gpts = [(float(r["t_utc"]), float(r["gear"])) for r in vl.get("gear", [])
            if isinstance(r.get("gear"), (int, float))]
    if gpts:
        gpts.sort()
        out.append(TimeSeries("dash_gear", np.array([p[0] for p in gpts]),
                              np.array([p[1] for p in gpts]), unit="gear"))
    return out


def _edge_imu_references(session: Session) -> list[TimeSeries]:
    m = session.edge_motion
    if not m:
        return []
    t = m["t_utc"]
    acc = np.vstack([m["acc_x"], m["acc_y"], m["acc_z"]])
    mag = np.linalg.norm(acc, axis=0)
    return [
        TimeSeries("edge_imu_accel_mag", t, mag, unit="g", clock="edge"),
        TimeSeries("edge_imu_yaw_rate", t, m["rot_z"], unit="rad/s", clock="edge"),
    ]


def _edge_gps_references(session: Session) -> list[TimeSeries]:
    loc = session.edge_location
    if not loc or "speed" not in loc:
        return []
    t, s = loc["t_utc"], loc["speed"].copy()
    if not np.any(s >= 0):
        return []
    s[s < 0] = np.nan
    return [
        TimeSeries("edge_gps_speed", t, s, unit="m/s", clock="edge"),
        TimeSeries("edge_gps_speed_kmh", t, s * 3.6, unit="km/h", clock="edge"),
    ]


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
