"""End-to-end test: synthesize a drive, then prove the pipeline decodes it.

This is the project's headline guarantee, exercised with zero hardware: generate
a session whose ground truth we know, run align -> extract -> identify, and
assert that speed and RPM are recovered from raw CAN bytes, that the injected
clock offset is estimated, and that a DBC is emitted.
"""

from __future__ import annotations

from canrosetta.dbc import to_dbc
from canrosetta.identify import identify_session
from canrosetta.session import load_session
from canrosetta.synth import BODY_ID, RPM_ID, SPEED_ID, generate


def _run(tmp_path):
    root = generate(tmp_path / "sess", duration_s=120.0, edge_clock_offset_s=0.7)
    session = load_session(root)
    return session, identify_session(session, hz=10.0)


def test_alignment_recovers_clock_offset(tmp_path):
    _, result = _run(tmp_path)
    # edge clock ran 0.7s ahead => delta to reach companion is about -0.7s
    assert result.alignment.confidence > 0.9
    assert abs(result.alignment.delta - (-0.7)) < 0.25


def test_speed_signal_identified(tmp_path):
    _, result = _run(tmp_path)
    top = result.per_reference["gps_speed_kmh"][0]
    assert top.candidate.arb_id == SPEED_ID
    assert top.candidate.byte_offset == 1
    assert top.candidate.width_bytes == 2
    assert top.candidate.endian == "big"
    assert abs(top.r) > 0.98
    # scale should recover the 0.01 km/h factor baked into synth
    assert abs(top.scale - 0.01) < 0.01


def test_rpm_signal_identified(tmp_path):
    _, result = _run(tmp_path)
    # rpm appears both as an OBD reference and (ideally) via GPS-correlated speed;
    # the OBD reference is the direct one.
    ref = "obd_engine_rpm"
    assert ref in result.per_reference
    top = result.per_reference[ref][0]
    assert top.candidate.arb_id == RPM_ID
    assert top.candidate.byte_offset == 0
    assert top.candidate.width_bytes == 2
    # RPM is a gear-shift sawtooth sampled at 2 Hz OBD, so its correlation is
    # slightly below the smooth speed signal's, but still a confident match.
    assert abs(top.r) > 0.9


def test_confident_mappings_and_dbc(tmp_path):
    _, result = _run(tmp_path)
    confident = result.confident(min_r=0.9)
    refs = {h.reference for h in confident}
    assert "gps_speed_kmh" in refs

    dbc = to_dbc(result, min_r=0.9)
    assert "BO_" in dbc and "SG_" in dbc
    assert f"BO_ {SPEED_ID}" in dbc  # speed message present


def test_sync_marker_alignment(tmp_path):
    # a deliberate marker on the companion clock + a matching edge-IMU decel spike
    import json

    from canrosetta.align import estimate_from_markers
    from canrosetta.session import load_session

    root = tmp_path / "m"
    (root / "edge").mkdir(parents=True)
    offset = 0.3  # edge clock runs 0.3 s behind the marker time here
    with (root / "edge" / "motion.jsonl").open("w") as fh:
        for k in range(500):
            t = k * 0.01
            ax = -0.25 if abs(t - 2.0) < 0.05 else 0.0  # sharp decel at edge t=2.0
            fh.write(json.dumps({"t_utc": round(t, 3), "acc": [ax, 0.0, 0.98],
                                 "rot": [0, 0, 0]}) + "\n")
    (root / "can").mkdir(exist_ok=True)
    (root / "can" / "frames.jsonl").write_text(json.dumps({
        "t_mono": 0.0, "t_utc": 0.0, "arb_id": 0x1, "is_extended": False,
        "dlc": 1, "data": "00", "direction": "rx"}) + "\n")
    (root / "manifest.json").write_text(json.dumps({
        "schema_version": "1.0.0", "session_id": "m", "created_utc": 0.0,
        "devices": [{"role": "edge", "kind": "autopi", "id": "e"}],
        "streams": [{"path": "can/frames.jsonl", "kind": "can_frames"},
                    {"path": "edge/motion.jsonl", "kind": "motion"}],
        "sync_markers": [{"kind": "brake_pulse", "t_utc": 2.0 + offset}]}))

    align = estimate_from_markers(load_session(root))
    assert align is not None and align.method == "sync_marker"
    assert abs(align.delta - offset) < 0.05
    assert align.confidence > 0.9


def test_edge_onboard_sensors_identify_speed(tmp_path):
    _, result = _run(tmp_path)
    # the AutoPi's own GPS (edge clock) should also pin the speed frame
    assert "edge_gps_speed_kmh" in result.per_reference
    top = result.per_reference["edge_gps_speed_kmh"][0]
    assert top.candidate.arb_id == SPEED_ID
    assert abs(top.r) > 0.98


def test_dashboard_labels_identify_telltale_and_gear(tmp_path):
    # references derived from the (synthetic) filmed dashboard should pin
    # signals no OBD PID exposes: a turn-signal telltale bit and the gear enum.
    _, result = _run(tmp_path)

    assert "telltale_turn_signal" in result.per_reference
    tt = result.per_reference["telltale_turn_signal"][0]
    assert tt.candidate.arb_id == BODY_ID
    assert abs(tt.r) > 0.5

    assert "dash_gear" in result.per_reference
    g = result.per_reference["dash_gear"][0]
    assert g.candidate.arb_id == BODY_ID
    assert g.candidate.byte_offset == 1
    assert abs(g.r) > 0.9


def test_noise_frame_yields_no_confident_signal(tmp_path):
    _, result = _run(tmp_path)
    # the random 0x2A0 frame must never win a reference at high confidence
    for hyps in result.per_reference.values():
        for h in hyps:
            if h.candidate.arb_id == 0x2A0:
                assert abs(h.r) < 0.9
