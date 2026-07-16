"""Tests for onboard sensor logging and the wake lock."""

import json

import jsonschema

from canrosetta_edge.config import EdgeConfig
from canrosetta_edge.power import AutoPiWakeLock, NoopWakeLock, make_wake_lock
from canrosetta_edge.sensors import (
    IioSensorSource,
    SensorLogger,
    SimulatedSensorSource,
    make_sensor_source,
)


def test_simulated_source_records_validate(motion_schema):
    src = SimulatedSensorSource()
    rec = {"t_utc": 1.0, **src.read_motion()}
    jsonschema.validate(rec, motion_schema)
    assert src.available()


def test_sensor_logger_writes_jsonl(tmp_path, motion_schema, location_schema):
    logger = SensorLogger(
        SimulatedSensorSource(),
        str(tmp_path / "edge" / "motion.jsonl"),
        str(tmp_path / "edge" / "location.jsonl"),
        rate_hz=100.0, location_rate_hz=20.0,
    )
    logger.start()
    import time
    time.sleep(0.5)
    logger.stop()
    assert logger.motion_count > 0
    motion = [json.loads(x) for x in open(tmp_path / "edge" / "motion.jsonl") if x.strip()]
    for rec in motion[:20]:
        jsonschema.validate(rec, motion_schema)


def test_make_sensor_source_defaults(tmp_path):
    cfg = EdgeConfig(transport="simulated", sensor_source="auto")
    assert isinstance(make_sensor_source(cfg), SimulatedSensorSource)
    cfg2 = EdgeConfig(sensors_enabled=False)
    assert make_sensor_source(cfg2).available() is False


def test_iio_source_absent_is_unavailable(tmp_path):
    src = IioSensorSource(base=str(tmp_path / "nonexistent"))
    assert src.available() is False
    assert src.read_motion() is None


def test_wake_lock_selection_and_noop():
    # simulated transport -> no-op wake lock (nothing to keep awake in dev)
    cfg = EdgeConfig(transport="simulated")
    lock = make_wake_lock(cfg)
    assert isinstance(lock, NoopWakeLock)
    with lock:  # context manager must not raise
        pass


def test_autopi_wake_lock_without_salt_is_inert():
    lock = AutoPiWakeLock(runner=None)
    assert lock.available() is False
    lock.acquire()  # must be a safe no-op when salt-call is absent
    lock.release()
