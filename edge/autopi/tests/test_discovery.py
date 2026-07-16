"""Discovery finds the simulated PIDs and VIN, and validates against schema."""

import jsonschema

from canrosetta_edge.config import EdgeConfig
from canrosetta_edge.discovery import discover
from canrosetta_edge.transport import SimulatedTransport


def test_fast_discovery_finds_pids_and_vin(discovery_schema):
    with SimulatedTransport() as t:
        result = discover(t, mode="fast")

    jsonschema.validate(result, discovery_schema)

    pids = result["obd"]["supported_pids"]
    # The three signals the simulated ECU exposes.
    assert "0x0C" in pids  # rpm
    assert "0x0D" in pids  # speed
    assert "0x05" in pids  # coolant temp

    # Every discovered, decodable PID produced a live sample with a value.
    sampled = {s["pid"] for s in result["obd"]["samples"] if s.get("value") is not None}
    assert {"0x0C", "0x0D", "0x05"} <= sampled

    # VIN DID responds on the standard ECU.
    assert "0xF190" in result["uds"]["responding_dids"]
    assert any(e["tx_id"] == "0x7E0" and "0xF190" in e["dids"]
               for e in result["uds"]["ecus"])


def test_slow_discovery_census_and_schema(discovery_schema):
    cfg = EdgeConfig(plain_can_census_s=1.0, brute_force_throttle_s=0.0,
                     obd_pid_min=0x00, obd_pid_max=0x20,
                     uds_did_min=0xF190, uds_did_max=0xF195)
    with SimulatedTransport() as t:
        result = discover(t, mode="slow", config=cfg)

    jsonschema.validate(result, discovery_schema)

    census = {e["arb_id"]: e for e in result["plain_can"]["arb_ids"]}
    # The periodic speed frame was seen many times with a plausible period.
    assert "0x3E9" in census
    assert census["0x3E9"]["count"] >= 2
    assert census["0x3E9"]["period_ms_est"] > 0
    # Its speed/counter bytes change; the constant frame's do not.
    assert census["0x3E9"]["changing_bytes"]
    if "0x100" in census:
        assert census["0x100"]["changing_bytes"] == []
