"""Message-role classification and passive command (causality) identification."""

from __future__ import annotations

import numpy as np

from canrosetta.references import build_references
from canrosetta.roles import (
    PERIODIC,
    SPORADIC,
    causality_lead,
    command_candidates,
    message_roles,
)
from canrosetta.session import TimeSeries, load_session
from canrosetta.synth import BRAKE_ID, generate, generate_command_demo


def test_periodic_frames_classified(tmp_path):
    session = load_session(generate(tmp_path / "s", duration_s=30.0))
    roles = message_roles(session)
    # the 50 Hz speed broadcast is periodic with ~20 ms cadence and low jitter
    from canrosetta.synth import SPEED_ID
    assert roles[SPEED_ID].role == PERIODIC
    assert abs(roles[SPEED_ID].period_ms - 20.0) < 5.0
    assert roles[SPEED_ID].jitter < 0.25


def test_sporadic_detection_on_irregular_times():
    from canrosetta.session import CanFrames

    # hand-build an irregular (bursty) arbitration ID
    t = np.array([0.0, 0.02, 0.04, 1.0, 1.02, 3.5, 3.52, 3.54])
    frames = CanFrames(
        t_mono=t, t_utc=t, arb_id=np.full(len(t), 0x321, dtype=np.uint32),
        is_extended=np.zeros(len(t), bool),
        data=[b"\x01\x02"] * len(t), direction=np.array(["rx"] * len(t), object),
    )
    from canrosetta.session import Session
    s = Session(root=None, manifest={}, frames=frames)  # type: ignore[arg-type]
    roles = message_roles(s)
    assert roles[0x321].role == SPORADIC


def test_causality_lead_sign_and_magnitude():
    hz = 20.0
    t = np.arange(0, 30, 1 / hz)
    effect_v = np.sin(t)
    lead = 0.3
    cause_v = np.sin(t + lead)  # cause happens 0.3 s BEFORE the effect
    cause = TimeSeries("cause", t, cause_v)
    effect = TimeSeries("effect", t, effect_v)
    lag, r = causality_lead(cause, effect, hz=hz)
    assert lag > 0.2 and lag < 0.4  # positive lead ~0.3 s
    assert abs(r) > 0.95


def test_brake_command_leads_deceleration(tmp_path):
    session = load_session(generate_command_demo(tmp_path / "cmd", actuation_lag_s=0.4))
    effects = [r for r in build_references(session) if r.name == "edge_imu_accel_long"]
    assert effects, "expected a signed longitudinal accel reference"
    cmds = command_candidates(session, effects, min_lead_s=0.1, min_r=0.5)
    assert "edge_imu_accel_long" in cmds
    top = cmds["edge_imu_accel_long"][0]
    assert top.arb_id == BRAKE_ID  # the brake command frame
    assert top.lead_s > 0.2  # it LEADS the deceleration (actuation lag ~0.4 s)
