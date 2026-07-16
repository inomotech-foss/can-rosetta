"""Stage 2 - time alignment.

The edge (CAN) clock and the companion (phone) clock are never perfectly
synchronized. We estimate a single residual offset ``delta`` such that

    edge_t_utc + delta  ≈  companion_t_utc

Coarse alignment trusts the manifest clock priors (usually near zero when both
devices use NTP/GPS). Fine alignment refines it by cross-correlating a pair of
*physically redundant* series each clock observes independently — preferably OBD
vehicle speed (edge) against GPS ground speed (companion). The lag that maximizes
their correlation is the residual offset.

Only the offset is estimated (a constant shift). Clock *drift* over a drive is
typically small; if it ever matters, this is where a piecewise/linear-drift
model would go.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .session import Session
from .signals import best_lag, common_grid, resample_uniform


@dataclass
class Alignment:
    delta: float  # seconds to ADD to edge t_utc to reach companion clock
    confidence: float  # |correlation| of the pair used, in [0, 1]
    method: str  # how delta was obtained
    pair: tuple[str, str] | None = None


def _manifest_prior(session: Session) -> float:
    edge_off = comp_off = 0.0
    for d in session.manifest.get("devices", []):
        clk = d.get("clock", {})
        if d.get("role") == "edge":
            edge_off = float(clk.get("utc_offset_est_s", 0.0))
        elif d.get("role") == "companion":
            comp_off = float(clk.get("utc_offset_est_s", 0.0))
    # both offsets are (device -> UTC); delta maps edge->companion
    return comp_off - edge_off


def _edge_obd_speed(session: Session) -> tuple[np.ndarray, np.ndarray] | None:
    samples = session.discovery.get("obd", {}).get("samples", [])
    pts = [
        (float(s["t_utc"]), float(s["value"]))
        for s in samples
        if s.get("name") == "vehicle_speed" and isinstance(s.get("value"), (int, float))
    ]
    if len(pts) < 8:
        return None
    pts.sort()
    return np.array([p[0] for p in pts]), np.array([p[1] for p in pts])


def _gps_speed_kmh(session: Session) -> tuple[np.ndarray, np.ndarray] | None:
    loc = session.location
    if not loc or "speed" not in loc:
        return None
    t, s = loc["t_utc"], loc["speed"].copy()
    if not np.any(s >= 0):
        return None
    s = np.where(s < 0, np.nan, s) * 3.6
    return t, s


def estimate_alignment(
    session: Session, *, hz: float = 10.0, max_lag_s: float = 5.0
) -> Alignment:
    prior = _manifest_prior(session)

    edge = _edge_obd_speed(session)
    gps = _gps_speed_kmh(session)
    if edge is None or gps is None:
        return Alignment(delta=prior, confidence=0.0, method="manifest_prior")

    et, ev = edge
    gt, gv = gps
    # apply coarse prior to the edge series, then refine
    et_adj = et + prior
    grid = common_grid(et_adj, gt, hz=hz)
    if len(grid) < 16:
        return Alignment(delta=prior, confidence=0.0, method="manifest_prior")

    ref = resample_uniform(gt, gv, grid, max_gap=2.0)  # companion GPS
    sig = resample_uniform(et_adj, ev, grid, max_gap=2.0)  # edge OBD
    lag, r = best_lag(ref, sig, hz=hz, max_lag_s=max_lag_s)
    # sig (edge) shifted by +lag lines up with ref (companion): edge->companion
    return Alignment(
        delta=prior + lag,
        confidence=abs(r),
        method="xcorr(obd_speed,gps_speed)",
        pair=("obd_vehicle_speed", "gps_speed_kmh"),
    )
