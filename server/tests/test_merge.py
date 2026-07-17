"""Merging edge + companion session parts into one session."""

from __future__ import annotations

import json
from pathlib import Path

from canrosetta.merge import merge_all, merge_parts, merge_status, scan_parts
from canrosetta.session import load_session


def _write(dir_: Path, rel: str, text: str) -> None:
    p = dir_ / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _edge_part(root: Path, sid: str) -> Path:
    d = root / f"{sid}-edge"
    _write(d, "manifest.json", json.dumps({
        "schema_version": "1.0.0", "session_id": sid, "created_utc": 1.0,
        "devices": [{"role": "edge", "kind": "autopi", "id": "e"}],
        "streams": [{"path": "can/frames.jsonl", "kind": "can_frames"},
                    {"path": "edge/motion.jsonl", "kind": "motion"}],
    }))
    _write(d, "can/frames.jsonl", json.dumps({
        "t_mono": 0.0, "t_utc": 0.0, "arb_id": 0x100, "is_extended": False,
        "dlc": 2, "data": "0102", "direction": "rx"}) + "\n")
    _write(d, "edge/motion.jsonl", json.dumps({
        "t_utc": 0.0, "acc": [0.1, 0.0, 0.98], "rot": [0, 0, 0]}) + "\n")
    return d


def _companion_part(root: Path, sid: str) -> Path:
    d = root / f"{sid}-phone"
    _write(d, "manifest.json", json.dumps({
        "schema_version": "1.0.0", "session_id": sid, "created_utc": 1.0,
        "devices": [{"role": "companion", "kind": "ios", "id": "p"}],
        "streams": [{"path": "phone/motion.jsonl", "kind": "motion"},
                    {"path": "phone/location.jsonl", "kind": "location"}],
    }))
    _write(d, "phone/motion.jsonl", json.dumps({
        "t_utc": 0.0, "acc": [0.0, 0.0, 1.0], "rot": [0, 0, 0]}) + "\n")
    _write(d, "phone/location.jsonl", json.dumps({
        "t_utc": 0.0, "lat": 48.0, "lon": 11.0, "speed": 5.0}) + "\n")
    return d


def test_merge_two_parts(tmp_path):
    root = tmp_path / "parts"
    root.mkdir()
    _edge_part(root, "drv1")
    _companion_part(root, "drv1")

    parts = scan_parts(root)["drv1"]
    assert len(parts) == 2
    out = merge_parts(parts, tmp_path / "merged" / "drv1")

    manifest = json.loads((out / "manifest.json").read_text())
    assert len(manifest["devices"]) == 2
    paths = {s["path"] for s in manifest["streams"]}
    assert {"can/frames.jsonl", "edge/motion.jsonl",
            "phone/motion.jsonl", "phone/location.jsonl"} <= paths

    session = load_session(out)
    assert len(session.frames) == 1
    assert session.motion and session.edge_motion and session.location


def test_status_reports_awaiting_when_counterpart_missing(tmp_path):
    root = tmp_path / "parts"
    root.mkdir()
    _edge_part(root, "lonely")  # edge only
    status = {s["session_id"]: s for s in merge_status(root)}
    assert status["lonely"]["complete"] is False
    assert status["lonely"]["missing"] == ["companion"]
    assert status["lonely"]["status"] == "awaiting"


def test_merge_all_only_merges_complete_groups(tmp_path):
    root = tmp_path / "parts"
    root.mkdir()
    _edge_part(root, "a")
    _companion_part(root, "a")
    _edge_part(root, "b")  # incomplete
    merged = merge_all(root, tmp_path / "out")
    assert len(merged) == 1
    assert merged[0].name == "a"
