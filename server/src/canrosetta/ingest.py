"""Ingest real-world CAN captures into the CAN-Rosetta session format.

Most public CAN datasets and the captures in the academic literature ship as
SocketCAN ``candump -L`` logs — the canonical Linux CAN text format:

    (1642612345.678901) can0 3C0#001A2B0055000000
    (1642612345.688901) can0 1F0#0AB4000000000000

This converts such a log into a session directory (``can/frames.jsonl`` + a
minimal ``manifest.json``) so it flows through the exact same align → extract →
identify pipeline as synthetic data. Bring your own reference signals (phone or
edge sensor JSONL, or OBD samples in ``discovery.json``) to decode it; without
references the extractor still runs and you can inspect candidate structure.

This keeps the project honest about real data: point it at any candump log —
your own drive, or a public dataset — and run ``canrosetta identify``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# (timestamp) interface id#hexdata      — id is hex; '#' then payload hex.
# extended IDs are 8 hex digits; a trailing 'R' marks RTR frames (skipped).
_LINE = re.compile(
    r"^\((?P<ts>\d+\.\d+)\)\s+(?P<iface>\S+)\s+(?P<id>[0-9A-Fa-f]+)#(?P<data>[0-9A-Fa-f]*)"
)


def parse_candump_line(line: str) -> dict | None:
    """Parse one ``candump -L`` line into a frame record, or None if it doesn't match."""
    m = _LINE.match(line.strip())
    if not m:
        return None
    arb_id = int(m.group("id"), 16)
    data = m.group("data")
    if len(data) % 2 != 0:
        return None
    ts = float(m.group("ts"))
    return {
        "t_mono": ts,
        "t_utc": ts,
        "channel": m.group("iface"),
        "arb_id": arb_id,
        "is_extended": len(m.group("id")) > 3,
        "dlc": len(data) // 2,
        "data": data.lower(),
        "direction": "rx",
        "probe_id": None,
    }


def from_candump(log_path: str | Path, out_dir: str | Path,
                 *, session_id: str | None = None, vehicle: dict | None = None) -> Path:
    """Convert a candump ``-L`` log into a session directory. Returns its path."""
    log_path = Path(log_path)
    out = Path(out_dir)
    (out / "can").mkdir(parents=True, exist_ok=True)

    n = 0
    channels: set[str] = set()
    t_first: float | None = None
    t_last: float | None = None
    with log_path.open("r", encoding="utf-8", errors="replace") as src, \
            (out / "can" / "frames.jsonl").open("w", encoding="utf-8") as dst:
        for line in src:
            rec = parse_candump_line(line)
            if rec is None:
                continue
            dst.write(json.dumps(rec) + "\n")
            n += 1
            channels.add(rec["channel"])
            t_first = rec["t_utc"] if t_first is None else min(t_first, rec["t_utc"])
            t_last = rec["t_utc"] if t_last is None else max(t_last, rec["t_utc"])

    if n == 0:
        raise ValueError(f"no candump lines parsed from {log_path} — is it `candump -L` format?")

    manifest = {
        "schema_version": "1.0.0",
        "session_id": session_id or log_path.stem,
        "created_utc": t_first or 0.0,
        "devices": [{
            "role": "edge", "kind": "candump-import", "id": "import",
            "sw_version": "canrosetta-ingest",
            "clock": {"source": "unknown", "utc_offset_est_s": 0.0, "err_est_s": 1.0},
        }],
        "streams": [{
            "path": "can/frames.jsonl", "kind": "can_frames", "rows": n,
            "t_start_utc": t_first, "t_end_utc": t_last,
        }],
    }
    if vehicle:
        manifest["vehicle"] = vehicle
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return out


# --------------------------------------------------------------------------- #
# comma2k19 (design validation)
# --------------------------------------------------------------------------- #
# comma2k19 (github.com/commaai/comma2k19, MIT) is our exact parallel corpus:
# raw CAN + IMU + GNSS from a real Civic/RAV4, with ground truth available via
# opendbc. It is ~100 GB of capnp logs, so this is *offline design-validation*
# tooling, never part of CI. The pure mapping helpers below are unit-tested; the
# capnp reader is a thin lazy wrapper feeding them.


def write_can_frames(frames, out_dir: str | Path) -> int:
    """Write ``(t_utc, arb_id, data_bytes, is_extended)`` tuples to a session.

    The testable core of any importer: it knows nothing about capnp/openpilot.
    Returns the number of frames written.
    """
    out = Path(out_dir)
    (out / "can").mkdir(parents=True, exist_ok=True)
    n = 0
    with (out / "can" / "frames.jsonl").open("w", encoding="utf-8") as fh:
        for t_utc, arb_id, data, is_extended in frames:
            data = bytes(data)
            fh.write(json.dumps({
                "t_mono": float(t_utc), "t_utc": float(t_utc), "channel": "can0",
                "arb_id": int(arb_id), "is_extended": bool(is_extended),
                "dlc": len(data), "data": data.hex(), "direction": "rx",
                "probe_id": None,
            }) + "\n")
            n += 1
    return n


def write_edge_motion(times, accel_xyz, out_dir: str | Path, gyro_xyz=None) -> int:
    """Write onboard IMU samples (edge clock) from parallel numpy-ish arrays."""
    import numpy as np

    out = Path(out_dir)
    (out / "edge").mkdir(parents=True, exist_ok=True)
    times = np.asarray(times, dtype=float)
    accel = np.asarray(accel_xyz, dtype=float)
    gyro = np.asarray(gyro_xyz, dtype=float) if gyro_xyz is not None else None
    with (out / "edge" / "motion.jsonl").open("w", encoding="utf-8") as fh:
        for i in range(len(times)):
            rec = {"t_utc": float(times[i]),
                   "acc": [float(x) for x in accel[i]],
                   "rot": [float(x) for x in (gyro[i] if gyro is not None else (0.0, 0.0, 0.0))]}
            fh.write(json.dumps(rec) + "\n")
    return len(times)


def from_comma2k19(segment_dir: str | Path, out_dir: str | Path) -> Path:
    """Convert one comma2k19 segment into a session directory (offline).

    Reads raw CAN from the segment's ``raw_log`` via openpilot's ``LogReader``
    (imported lazily — ``pip install openpilot-tools`` / clone commaai/openpilot),
    and the IMU/GNSS from the segment's ``processed_log`` numpy arrays. Requires
    the dataset locally; not exercised in CI.
    """
    import numpy as np

    seg = Path(segment_dir)
    out = Path(out_dir)

    try:
        from tools.lib.logreader import LogReader  # type: ignore
    except ImportError as exc:  # noqa: TRY003
        raise ImportError(
            "reading comma2k19 raw CAN needs openpilot tools on PYTHONPATH; see "
            "github.com/commaai/openpilot (tools/lib/logreader). This is offline "
            "design-validation tooling, not part of the test suite."
        ) from exc

    def can_frames():
        raw = next(p for p in (seg / "raw_log.bz2", seg / "rlog.bz2", seg / "rlog")
                   if p.exists())
        for msg in LogReader(str(raw)):
            if msg.which() == "can":
                for c in msg.can:
                    if not c.src:  # bus 0 only; skip echo/other buses
                        yield (msg.logMonoTime * 1e-9, c.address, bytes(c.dat),
                               c.address > 0x7FF)

    n = write_can_frames(can_frames(), out)

    # IMU/GNSS from processed_log numpy arrays (paths per comma2k19 layout)
    proc = seg / "processed_log"
    acc_t = proc / "IMU" / "accelerometer" / "t"
    acc_v = proc / "IMU" / "accelerometer" / "value"
    if acc_t.exists() and acc_v.exists():
        gyro_v = proc / "IMU" / "gyro" / "value"
        write_edge_motion(
            np.load(acc_t), np.load(acc_v), out,
            gyro_xyz=np.load(gyro_v) if gyro_v.exists() else None,
        )

    manifest = {
        "schema_version": "1.0.0", "session_id": seg.name,
        "created_utc": 0.0,
        "devices": [{"role": "edge", "kind": "comma2k19", "id": "comma",
                     "sw_version": "canrosetta-ingest",
                     "clock": {"source": "gps", "utc_offset_est_s": 0.0, "err_est_s": 0.1}}],
        "streams": [{"path": "can/frames.jsonl", "kind": "can_frames", "rows": n}],
    }
    if (out / "edge" / "motion.jsonl").exists():
        manifest["streams"].append({"path": "edge/motion.jsonl", "kind": "motion"})
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return out
