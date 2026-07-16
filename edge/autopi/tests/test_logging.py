"""Logger captures periodic frames; every row validates against the schema."""

import json
import os

import jsonschema

from canrosetta_edge.logging_ import JsonlFrameWriter, Poller, capture
from canrosetta_edge.transport import SimulatedTransport


def _read_jsonl(path):
    with open(path, "r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def test_capture_records_valid_periodic_frames(tmp_path, can_frame_schema):
    path = os.path.join(tmp_path, "frames.jsonl")
    with SimulatedTransport() as t:
        writer = JsonlFrameWriter(path)
        poller = Poller(t, pids=[0x0C, 0x0D, 0x05], channel="can0")
        try:
            n = capture(t, writer, duration=1.0, poller=poller, poll_rate_hz=5.0)
        finally:
            writer.close()

    assert n > 0
    rows = _read_jsonl(path)
    assert len(rows) == n

    for row in rows:
        jsonschema.validate(row, can_frame_schema)
        # data hex length is exactly 2*dlc.
        assert len(row["data"]) == 2 * row["dlc"]

    arb_ids = {r["arb_id"] for r in rows}
    # Both simulated periodic broadcast frames were captured.
    assert 0x3E9 in arb_ids  # speed
    assert 0x3EA in arb_ids  # rpm

    # The poller's induced traffic is tagged tx + probe_id.
    tx = [r for r in rows if r["direction"] == "tx"]
    assert tx, "expected polled probe frames"
    assert all(r["probe_id"] for r in tx)
    # Poller collected decoded reference samples.
    assert poller.samples
    assert any(s["pid"] == "0x0D" and s.get("value") is not None
               for s in poller.samples)


def test_jsonl_writer_is_append_resumable(tmp_path):
    path = os.path.join(tmp_path, "frames.jsonl")
    with SimulatedTransport() as t:
        w1 = JsonlFrameWriter(path)
        capture(t, w1, duration=0.4)
        w1.close()
        first = w1.count
    assert first > 0

    with SimulatedTransport() as t:
        w2 = JsonlFrameWriter(path)          # resume existing file
        assert w2.count == first             # counted prior rows
        capture(t, w2, duration=0.4)
        w2.close()

    total = len(_read_jsonl(path))
    assert total == w2.count > first
