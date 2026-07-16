"""Engine state-machine tests against the simulated transport (no hardware)."""

import json
import os

import jsonschema

from canrosetta_edge.config import EdgeConfig
from canrosetta_edge.engine import IDLE, Busy, Engine


def _engine(tmp_path) -> Engine:
    cfg = EdgeConfig(transport="simulated", output_dir=str(tmp_path),
                     sensor_source="simulated", sensor_rate_hz=50.0)
    return Engine(cfg, device_id="test-edge")


def test_discovery_then_status(tmp_path, discovery_schema, manifest_schema):
    eng = _engine(tmp_path)
    info = eng.start_session()
    assert info["session_id"]
    eng.start_discovery("fast")
    eng.wait()
    assert eng.state == IDLE
    assert eng.error is None

    result = eng.read_discovery()
    assert result["obd"]["supported_pids"]  # found the simulated PIDs
    jsonschema.validate(result, discovery_schema)

    manifest = json.loads(open(os.path.join(eng.layout.root, "manifest.json")).read())
    jsonschema.validate(manifest, manifest_schema)


def test_logging_captures_frames_and_edge_sensors(tmp_path, can_frame_schema,
                                                  motion_schema, manifest_schema):
    eng = _engine(tmp_path)
    eng.start_session()
    eng.start_discovery("fast")
    eng.wait()
    eng.start_logging(duration_s=1.5)
    eng.wait()
    assert eng.state == IDLE and eng.error is None
    assert eng.status()["stats"]["frames"] > 0

    # onboard IMU was logged beside CAN, on the edge clock
    motion_path = eng.layout.edge_motion_path
    assert os.path.exists(motion_path)
    lines = [json.loads(x) for x in open(motion_path) if x.strip()]
    assert len(lines) > 0
    for rec in lines[:50]:
        jsonschema.validate(rec, motion_schema)

    # manifest lists the edge motion stream
    manifest = json.loads(open(os.path.join(eng.layout.root, "manifest.json")).read())
    jsonschema.validate(manifest, manifest_schema)
    kinds = {(s["path"], s["kind"]) for s in manifest["streams"]}
    assert ("edge/motion.jsonl", "motion") in kinds


def test_busy_rejects_second_job(tmp_path):
    eng = _engine(tmp_path)
    eng.start_session()
    eng.start_logging(duration_s=1.0)
    try:
        started = False
        try:
            eng.start_discovery("fast")
            started = True
        except Busy:
            started = False
        assert not started, "engine should reject a second concurrent job"
    finally:
        eng.stop()


def test_run_does_discovery_then_logging(tmp_path):
    eng = _engine(tmp_path)
    eng.start_session()
    eng.start_run("fast", duration_s=1.0)
    eng.wait()
    assert eng.state == IDLE and eng.error is None
    assert eng.read_discovery()["obd"]["supported_pids"]
    assert eng.status()["stats"]["frames"] > 0
