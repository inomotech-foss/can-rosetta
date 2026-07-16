"""Ingest a real-format candump -L log and run it through the pipeline."""

from __future__ import annotations

from canrosetta.extract import extract_session
from canrosetta.ingest import from_candump, parse_candump_line
from canrosetta.session import load_session

SAMPLE = """\
(1642612345.678901) can0 3C0#001A2B0055000000
(1642612345.688901) can0 1F0#0AB40000
(1642612345.690000) can0 18DAF110#0201020304050607
bogus line that should be ignored
(1642612345.698901) can0 3C0#001B2C0055000000
"""


def test_parse_line_variants():
    std = parse_candump_line("(1642612345.678901) can0 3C0#001A2B0055000000")
    assert std["arb_id"] == 0x3C0 and std["is_extended"] is False and std["dlc"] == 8
    ext = parse_candump_line("(1.0) can0 18DAF110#0201")
    assert ext["arb_id"] == 0x18DAF110 and ext["is_extended"] is True
    assert parse_candump_line("garbage") is None


def test_from_candump_builds_loadable_session(tmp_path):
    log = tmp_path / "drive.log"
    log.write_text(SAMPLE)
    out = from_candump(log, tmp_path / "sess", vehicle={"make": "Real"})

    session = load_session(out)
    assert len(session.frames) == 4  # bogus line skipped
    by_id = session.frames.by_id()
    assert 0x3C0 in by_id and 0x1F0 in by_id
    # the pipeline's extractor runs on the imported real-format frames
    cands = extract_session(by_id)
    assert isinstance(cands, list)


def test_empty_log_raises(tmp_path):
    log = tmp_path / "empty.log"
    log.write_text("not a candump line\n")
    import pytest
    with pytest.raises(ValueError):
        from_candump(log, tmp_path / "sess")


def test_comma2k19_mapping_helpers_build_loadable_session(tmp_path):
    # the capnp reader needs the dataset + openpilot, but the mapping core doesn't
    from canrosetta.ingest import write_can_frames, write_edge_motion

    frames = [
        (1000.00, 0x3C0, bytes([0, 0x1A, 0x2B, 0, 0x55, 0, 0, 0]), False),
        (1000.02, 0x1F0, bytes([0x0A, 0xB4, 0, 0]), False),
        (1000.04, 0x18DAF110, bytes([1, 2, 3, 4]), True),
    ]
    assert write_can_frames(frames, tmp_path / "sess") == 3
    write_edge_motion([1000.0, 1000.01], [[0.1, 0.0, 0.98], [0.2, 0.0, 0.97]],
                      tmp_path / "sess", gyro_xyz=[[0, 0, 0.01], [0, 0, 0.02]])
    (tmp_path / "sess" / "manifest.json").write_text(
        '{"schema_version":"1.0.0","session_id":"s","created_utc":0,'
        '"devices":[{"role":"edge","kind":"comma2k19","id":"c"}],'
        '"streams":[{"path":"can/frames.jsonl","kind":"can_frames"}]}'
    )
    session = load_session(tmp_path / "sess")
    assert len(session.frames) == 3
    assert 0x18DAF110 in session.frames.by_id()
    assert len(session.edge_motion["t_utc"]) == 2
