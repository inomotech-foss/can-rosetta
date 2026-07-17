"""EV end-to-end: identify battery current (signed, regen-negative) and SoC."""

from __future__ import annotations

import numpy as np

from canrosetta.ev import battery_power, soc_from_current
from canrosetta.identify import identify_session
from canrosetta.session import load_session
from canrosetta.synth import BATT_ID, generate_ev


def _run(tmp_path):
    root = generate_ev(tmp_path / "ev", duration_s=120.0)
    return identify_session(load_session(root), hz=10.0)


def test_battery_current_identified_via_signed_accel(tmp_path):
    result = _run(tmp_path)
    # signed longitudinal accel (edge IMU, on the CAN clock) tracks pack current:
    # positive under drive, negative under regen.
    assert "edge_imu_accel_long" in result.per_reference
    top = result.per_reference["edge_imu_accel_long"][0]
    assert top.candidate.arb_id == BATT_ID
    assert top.candidate.byte_offset == 2  # current starts at byte 2
    # regen makes current go negative, so the SIGNED reading wins over unsigned
    assert top.candidate.signed is True
    assert top.r > 0.9  # positive: more current => more forward accel


def test_soc_identified_via_ev_obd(tmp_path):
    result = _run(tmp_path)
    assert "ev_hv_battery_soc" in result.per_reference
    top = result.per_reference["ev_hv_battery_soc"][0]
    assert top.candidate.arb_id == BATT_ID
    # boundary detection may pick a superset span; require it to COVER bytes 4-5
    # (the true SoC field) — exact-boundary refinement is future byte-clustering work.
    c = top.candidate
    assert c.byte_offset <= 4 and c.byte_offset + c.width_bytes >= 6
    assert abs(top.r) > 0.9


def test_regen_reference_exists(tmp_path):
    session = load_session(generate_ev(tmp_path / "ev", duration_s=60.0))
    from canrosetta.ev import ev_references
    names = {r.name for r in ev_references(session)}
    assert "edge_regen_brake" in names or "regen_brake" in names


def test_ev_physics_helpers():
    assert battery_power(np.array([360.0]), np.array([100.0]))[0] == 36000.0
    t = np.arange(0, 3600, 1.0)  # 1 h
    soc = soc_from_current(t, np.full_like(t, 50.0), capacity_ah=50.0, soc0=100.0)
    assert abs(soc[-1] - 0.0) < 2.0  # 50 A for 1 h drains a 50 Ah pack ~ 100%
