"""Session layout + edge manifest writing.

The edge device writes a *session part*: it lays out the ``can/`` directory and
emits a ``manifest.json`` describing only its own device (role ``edge``, kind
``autopi``) plus the streams it produced. The server later merges this part with
the companion's part that shares the same ``session_id``.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import time
import uuid
from typing import List, Optional

SCHEMA_VERSION = "1.0.0"
SW_VERSION = "can-rosetta-edge/0.1.0"


def hash_vin(vin: str) -> str:
    """Return the ``sha256:...`` hash of a VIN; raw VINs are never stored."""
    return "sha256:" + hashlib.sha256(vin.strip().upper().encode("ascii")).hexdigest()


def default_device_id() -> str:
    host = socket.gethostname().split(".")[0]
    return f"autopi-{host}"


class SessionLayout:
    """Filesystem layout for one session directory."""

    def __init__(self, output_dir: str, session_id: str):
        self.session_id = session_id
        self.root = os.path.abspath(output_dir)
        self.can_dir = os.path.join(self.root, "can")
        self.edge_dir = os.path.join(self.root, "edge")

    def ensure(self) -> "SessionLayout":
        os.makedirs(self.can_dir, exist_ok=True)
        return self

    @property
    def frames_base(self) -> str:
        """Path stem for the frame log (writer appends .parquet / .jsonl)."""
        return os.path.join(self.can_dir, "frames")

    @property
    def discovery_path(self) -> str:
        return os.path.join(self.can_dir, "discovery.json")

    @property
    def manifest_path(self) -> str:
        return os.path.join(self.root, "manifest.json")

    @property
    def edge_motion_path(self) -> str:
        """Onboard IMU log (edge clock) — see canrosetta_edge.sensors."""
        return os.path.join(self.edge_dir, "motion.jsonl")

    @property
    def edge_location_path(self) -> str:
        return os.path.join(self.edge_dir, "location.jsonl")


def new_session_id() -> str:
    return str(uuid.uuid4())


def build_manifest(session_id: str,
                   device_id: Optional[str] = None,
                   clock_source: str = "ntp",
                   utc_offset_est_s: float = 0.0,
                   err_est_s: float = 0.03,
                   vehicle: Optional[dict] = None,
                   streams: Optional[List[dict]] = None,
                   created_utc: Optional[float] = None) -> dict:
    """Build a schema-valid manifest for the edge part of a session."""
    manifest: dict = {
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id,
        "created_utc": created_utc if created_utc is not None else time.time(),
        "devices": [
            {
                "role": "edge",
                "kind": "autopi",
                "id": device_id or default_device_id(),
                "sw_version": SW_VERSION,
                "clock": {
                    "source": clock_source,
                    "utc_offset_est_s": utc_offset_est_s,
                    "err_est_s": err_est_s,
                },
            }
        ],
        "streams": streams or [],
    }
    if vehicle:
        manifest["vehicle"] = vehicle
    return manifest


def write_manifest(path: str, manifest: dict) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    return path


def write_discovery(path: str, discovery: dict) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(discovery, fh, indent=2)
    return path


def frames_stream_entry(rel_path: str, writer) -> dict:
    """Build a manifest ``streams`` entry for the frame log."""
    entry = {"path": rel_path, "kind": "can_frames", "rows": writer.count}
    if writer.t_start_utc is not None:
        entry["t_start_utc"] = writer.t_start_utc
    if writer.t_end_utc is not None:
        entry["t_end_utc"] = writer.t_end_utc
    return entry
