"""Load a session directory into memory.

A session on disk is described in docs/data-format.md. This module turns it into
plain in-memory objects (numpy-backed) that the rest of the pipeline consumes.
Raw inputs are treated as immutable; nothing here writes back into the session.

Reading is defensive: session files are untrusted input, so we validate shapes
and reject anything that doesn't look right rather than trusting it blindly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

SUPPORTED_SCHEMA_MAJOR = 1


@dataclass
class CanFrames:
    """All CAN frames in a session, in acquisition order.

    Timestamps are seconds. ``data`` is a list of ``bytes`` (variable length,
    matching each frame's DLC). Use :meth:`by_id` to get per-arbitration-ID
    matrices, which is what candidate extraction works on.
    """

    t_mono: np.ndarray  # float64 [n]
    t_utc: np.ndarray  # float64 [n]
    arb_id: np.ndarray  # uint32 [n]
    is_extended: np.ndarray  # bool [n]
    data: list[bytes]  # [n]
    direction: np.ndarray  # object/str [n]

    def __len__(self) -> int:
        return len(self.data)

    def by_id(self, *, rx_only: bool = True) -> dict[int, FramesForId]:
        """Group frames by arbitration ID.

        By default only passively-sniffed (``rx``) frames are grouped, since
        induced (``tx``) traffic from the discovery probe isn't organic bus
        broadcast and shouldn't be mined for plain-CAN signals.
        """
        groups: dict[int, list[int]] = {}
        for i in range(len(self.data)):
            if rx_only and self.direction[i] != "rx":
                continue
            groups.setdefault(int(self.arb_id[i]), []).append(i)
        out: dict[int, FramesForId] = {}
        for aid, idx in groups.items():
            idx_arr = np.asarray(idx, dtype=np.int64)
            width = max(len(self.data[i]) for i in idx)
            mat = np.zeros((len(idx), width), dtype=np.uint8)
            for r, i in enumerate(idx):
                d = self.data[i]
                mat[r, : len(d)] = np.frombuffer(d, dtype=np.uint8)
            out[aid] = FramesForId(
                arb_id=aid,
                t=self.t_utc[idx_arr].copy(),
                t_mono=self.t_mono[idx_arr].copy(),
                payload=mat,
            )
        return out


@dataclass
class FramesForId:
    """Frames for a single arbitration ID as a dense byte matrix.

    ``t`` is the edge UTC clock (the base the alignment delta maps to the
    companion clock, so candidates built from it are directly comparable to
    references). ``t_mono`` is kept for period estimation, which must be immune
    to wall-clock steps.
    """

    arb_id: int
    t: np.ndarray  # float64 [m] edge UTC time — matching base
    t_mono: np.ndarray  # float64 [m] monotonic time — for timing/period only
    payload: np.ndarray  # uint8 [m, width]

    @property
    def width(self) -> int:
        return int(self.payload.shape[1])

    @property
    def period_est(self) -> float:
        """Median inter-frame interval in seconds (0 if fewer than 2 frames)."""
        if len(self.t_mono) < 2:
            return 0.0
        return float(np.median(np.diff(np.sort(self.t_mono))))


@dataclass
class TimeSeries:
    """A generic (time, value) series used for both references and candidates."""

    name: str
    t: np.ndarray
    v: np.ndarray
    unit: str = ""
    kind: str = "continuous"  # "continuous" | "event"
    clock: str = "companion"  # "companion" | "edge" — which clock t is on

    def __post_init__(self) -> None:
        self.t = np.asarray(self.t, dtype=np.float64)
        self.v = np.asarray(self.v, dtype=np.float64)
        if self.t.shape != self.v.shape:
            raise ValueError(f"{self.name}: t and v must have equal shape")


@dataclass
class Session:
    root: Path
    manifest: dict
    frames: CanFrames
    motion: dict[str, np.ndarray] = field(default_factory=dict)
    location: dict[str, np.ndarray] = field(default_factory=dict)
    edge_motion: dict[str, np.ndarray] = field(default_factory=dict)
    edge_location: dict[str, np.ndarray] = field(default_factory=dict)
    discovery: dict = field(default_factory=dict)
    labels: dict = field(default_factory=dict)
    # dashboard-video-derived label streams (see canrosetta.perception):
    #   {"dashboard": [rows], "telltales": [rows], "gear": [rows]}
    video_labels: dict[str, list] = field(default_factory=dict)

    @property
    def session_id(self) -> str:
        return self.manifest.get("session_id", self.root.name)


def _check_schema_version(obj: dict, what: str) -> None:
    ver = obj.get("schema_version", "1.0.0")
    try:
        major = int(str(ver).split(".", 1)[0])
    except ValueError as exc:  # noqa: TRY003
        raise ValueError(f"{what}: malformed schema_version {ver!r}") from exc
    if major != SUPPORTED_SCHEMA_MAJOR:
        raise ValueError(
            f"{what}: schema_version {ver} major={major} unsupported "
            f"(this build supports major {SUPPORTED_SCHEMA_MAJOR})"
        )


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _load_frames(can_dir: Path) -> CanFrames:
    parquet = can_dir / "frames.parquet"
    jsonl = can_dir / "frames.jsonl"
    if parquet.exists():
        rows = _load_frames_parquet(parquet)
    elif jsonl.exists():
        rows = _read_jsonl(jsonl)
    else:
        raise FileNotFoundError(f"no frames.parquet or frames.jsonl under {can_dir}")

    n = len(rows)
    t_mono = np.empty(n, dtype=np.float64)
    t_utc = np.empty(n, dtype=np.float64)
    arb_id = np.empty(n, dtype=np.uint32)
    is_ext = np.empty(n, dtype=bool)
    direction = np.empty(n, dtype=object)
    data: list[bytes] = []
    for i, r in enumerate(rows):
        t_mono[i] = r["t_mono"]
        t_utc[i] = r.get("t_utc", r["t_mono"])
        arb_id[i] = int(r["arb_id"])
        is_ext[i] = bool(r.get("is_extended", False))
        direction[i] = r.get("direction", "rx")
        raw = r["data"]
        data.append(bytes.fromhex(raw) if isinstance(raw, str) else bytes(raw))
    return CanFrames(t_mono, t_utc, arb_id, is_ext, data, direction)


def _load_frames_parquet(path: Path) -> list[dict]:
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:  # noqa: TRY003
        raise ImportError(
            "reading frames.parquet needs pyarrow; install canrosetta[parquet] "
            "or provide a frames.jsonl fallback"
        ) from exc
    table = pq.read_table(path)
    cols = table.to_pydict()
    n = table.num_rows
    out = []
    for i in range(n):
        d = cols["data"][i]
        if isinstance(d, (bytes, bytearray)):
            d = bytes(d).hex()
        out.append(
            {
                "t_mono": cols["t_mono"][i],
                "t_utc": cols.get("t_utc", cols["t_mono"])[i],
                "arb_id": cols["arb_id"][i],
                "is_extended": cols.get("is_extended", [False] * n)[i],
                "dlc": cols.get("dlc", [0] * n)[i],
                "data": d,
                "direction": cols.get("direction", ["rx"] * n)[i],
            }
        )
    return out


def _load_motion(path: Path) -> dict[str, np.ndarray]:
    rows = _read_jsonl(path)
    n = len(rows)
    out = {
        "t_utc": np.empty(n),
        "acc_x": np.empty(n),
        "acc_y": np.empty(n),
        "acc_z": np.empty(n),
        "rot_x": np.empty(n),
        "rot_y": np.empty(n),
        "rot_z": np.empty(n),
    }
    for i, r in enumerate(rows):
        out["t_utc"][i] = r["t_utc"]
        acc = r.get("acc", [np.nan, np.nan, np.nan])
        rot = r.get("rot", [np.nan, np.nan, np.nan])
        out["acc_x"][i], out["acc_y"][i], out["acc_z"][i] = acc
        out["rot_x"][i], out["rot_y"][i], out["rot_z"][i] = rot
    return out


def _load_location(path: Path) -> dict[str, np.ndarray]:
    rows = _read_jsonl(path)
    n = len(rows)
    out = {k: np.empty(n) for k in ("t_utc", "lat", "lon", "alt", "speed", "course")}
    for i, r in enumerate(rows):
        out["t_utc"][i] = r["t_utc"]
        out["lat"][i] = r["lat"]
        out["lon"][i] = r["lon"]
        out["alt"][i] = r.get("alt", np.nan)
        out["speed"][i] = r.get("speed", -1.0)
        out["course"][i] = r.get("course", -1.0)
    return out


def load_session(root: str | Path) -> Session:
    """Load a session directory into a :class:`Session`."""
    root = Path(root)
    if not root.is_dir():
        raise NotADirectoryError(f"session root {root} is not a directory")

    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        _check_schema_version(manifest, "manifest")
    else:
        manifest = {"schema_version": "1.0.0", "session_id": root.name}

    frames = _load_frames(root / "can")

    motion: dict[str, np.ndarray] = {}
    location: dict[str, np.ndarray] = {}
    if (root / "phone" / "motion.jsonl").exists():
        motion = _load_motion(root / "phone" / "motion.jsonl")
    if (root / "phone" / "location.jsonl").exists():
        location = _load_location(root / "phone" / "location.jsonl")

    # the AutoPi's own onboard sensors, on the edge clock (see docs/data-format.md)
    edge_motion: dict[str, np.ndarray] = {}
    edge_location: dict[str, np.ndarray] = {}
    if (root / "edge" / "motion.jsonl").exists():
        edge_motion = _load_motion(root / "edge" / "motion.jsonl")
    if (root / "edge" / "location.jsonl").exists():
        edge_location = _load_location(root / "edge" / "location.jsonl")

    discovery: dict = {}
    disc_path = root / "can" / "discovery.json"
    if disc_path.exists():
        discovery = json.loads(disc_path.read_text(encoding="utf-8"))
        _check_schema_version(discovery, "discovery")

    labels: dict = {}
    ann = root / "labels" / "annotations.json"
    if ann.exists():
        labels = json.loads(ann.read_text(encoding="utf-8"))

    video_labels: dict[str, list] = {}
    for key, fname in (("dashboard", "dashboard_ocr.jsonl"),
                       ("telltales", "telltales.jsonl"), ("gear", "gear.jsonl")):
        p = root / "labels" / fname
        if p.exists():
            video_labels[key] = _read_jsonl(p)

    return Session(
        root=root,
        manifest=manifest,
        frames=frames,
        motion=motion,
        location=location,
        edge_motion=edge_motion,
        edge_location=edge_location,
        discovery=discovery,
        labels=labels,
        video_labels=video_labels,
    )
