"""Unidentified / residual structured-signal clustering."""

from __future__ import annotations

import json
import math

import numpy as np

from canrosetta.clusters import cluster_series, unidentified_signals
from canrosetta.session import TimeSeries, load_session


def test_cluster_series_groups_correlated():
    t = np.linspace(0, 20, 400)
    a = np.sin(t)
    series = [
        TimeSeries("A", t, a),
        TimeSeries("B", t, 3 * a + 1 + np.random.default_rng(0).normal(0, 0.02, t.shape)),
        TimeSeries("C", t, np.cos(3.1 * t)),  # unrelated
    ]
    clusters = cluster_series(series, hz=20.0, cluster_r=0.8)
    big = max(clusters, key=len)
    assert set(big) == {"A", "B"}
    assert ["C"] in clusters


def _write_frame(fh, t_mono, arb_id, raw16):
    data = bytes([(raw16 >> 8) & 0xFF, raw16 & 0xFF, 0, 0, 0, 0, 0, 0])
    fh.write(json.dumps({
        "t_mono": round(t_mono, 3), "t_utc": round(t_mono, 3), "arb_id": arb_id,
        "is_extended": False, "dlc": 8, "data": data.hex(), "direction": "rx"}) + "\n")


def test_unidentified_cluster_with_no_references(tmp_path):
    root = tmp_path / "sess"
    (root / "can").mkdir(parents=True)
    n = 600
    with (root / "can" / "frames.jsonl").open("w") as fh:
        for k in range(n):
            t = k * 0.05
            shared = int(2000 + 800 * math.sin(t / 2))  # 0x200 and 0x201 share this
            indep = int(1500 + 500 * math.cos(t / 1.3 + 1))  # 0x202 independent
            _write_frame(fh, t, 0x200, shared)
            _write_frame(fh, t, 0x201, shared)
            _write_frame(fh, t, 0x202, indep)
    (root / "manifest.json").write_text(json.dumps({
        "schema_version": "1.0.0", "session_id": "u", "created_utc": 0.0,
        "devices": [{"role": "edge", "kind": "autopi", "id": "e"}],
        "streams": [{"path": "can/frames.jsonl", "kind": "can_frames"}]}))

    res = unidentified_signals(load_session(root), cluster_r=0.9)
    # no references -> everything is "unidentified"; 0x200 & 0x201 co-vary -> one cluster
    big = max(res.clusters, key=len)
    assert set(big) == {"0x200", "0x201"}
