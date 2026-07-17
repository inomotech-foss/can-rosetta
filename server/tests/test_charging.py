"""EV charging session: identify charge state, AC metering, and SoC while parked."""

from __future__ import annotations

import numpy as np

from canrosetta.charging import AC, DC, ac_power, classify_mode, infer_phase_count
from canrosetta.identify import identify_session
from canrosetta.session import load_session
from canrosetta.synth import BATT_ID, CHARGE_ID, generate_ev_charging


def _run(tmp_path):
    root = generate_ev_charging(tmp_path / "chg", duration_s=120.0)
    return identify_session(load_session(root), hz=5.0)


def _covers(c, lo, hi):  # candidate byte-span covers [lo, hi)
    return c.byte_offset <= lo and c.byte_offset + max(c.width_bytes, 1) >= hi


# signals that switch on/off with the charge event are mutually collinear within
# one session, so they resolve to the charging-signal *group* (battery or charge
# module), not a unique field. Only the multi-level state and the rising SoC have
# distinctive shapes and pin a unique field.
CHARGE_GROUP = {CHARGE_ID, BATT_ID}


def test_charge_state_uniquely_identified(tmp_path):
    result = _run(tmp_path)
    # the 5-level charge-state enum has a distinctive shape -> unique field
    assert "dash_charge_state" in result.per_reference
    top = result.per_reference["dash_charge_state"][0]
    assert top.candidate.arb_id == CHARGE_ID and top.candidate.byte_offset == 0
    assert abs(top.r) > 0.9


def test_charging_active_and_ac_current_found_in_group(tmp_path):
    result = _run(tmp_path)
    # charge-active flag and AC current switch with the charge event -> found at
    # high correlation somewhere in the charging-signal group (collinear).
    tt = result.per_reference["telltale_charging"][0]
    assert tt.candidate.arb_id in CHARGE_GROUP and abs(tt.r) > 0.9
    ac = result.per_reference["dash_ac_current"][0]
    assert ac.candidate.arb_id in CHARGE_GROUP and abs(ac.r) > 0.9


def test_soc_rises_and_is_identified(tmp_path):
    result = _run(tmp_path)
    top = result.per_reference["ev_hv_battery_soc"][0]
    assert top.candidate.arb_id == BATT_ID and _covers(top.candidate, 4, 6)
    assert top.r > 0.9  # SoC rises during charge


def test_mode_and_phase_helpers():
    ac_i = np.array([16.0, 16.0, 16.0])
    assert classify_mode(ac_i, None) == AC
    assert classify_mode(np.zeros(3), np.array([125.0, 125.0])) == DC
    assert infer_phase_count([ac_i, ac_i, ac_i]) == 3
    assert infer_phase_count([ac_i, np.zeros(3), np.zeros(3)]) == 1
    # 3-phase 230 V * 16 A ~ 11 kW
    assert abs(ac_power(np.array([230.0]), np.array([16.0]), phases=3)[0] - 11040.0) < 1.0
