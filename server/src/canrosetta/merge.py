"""Merge session *parts* into one session.

The edge (AutoPi) and companion (phone) each record a session *part* keyed by a
shared ``session_id`` and upload independently. The server stitches parts that
share an id into a single session directory before identification — the step the
architecture promised but that lived only in prose until now.

A part is "awaiting merge" until its counterpart arrives: a drive normally has an
**edge** part (CAN + onboard sensors) and a **companion** part (phone sensors +
video). ``merge_status`` reports which ids are complete and which are missing a
role; ``merge_parts`` performs the stitch.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA = "1.0.0"


@dataclass
class SessionPart:
    dir: Path
    session_id: str
    roles: list[str]  # device roles this part carries (edge / companion)
    manifest: dict = field(default_factory=dict)


def _read_manifest(d: Path) -> dict | None:
    p = d / "manifest.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def scan_parts(root: str | Path) -> dict[str, list[SessionPart]]:
    """Group immediate sub-directory session parts under ``root`` by session_id."""
    root = Path(root)
    groups: dict[str, list[SessionPart]] = {}
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        m = _read_manifest(d)
        if not m:
            continue
        sid = m.get("session_id", d.name)
        roles = [dev.get("role", "unknown") for dev in m.get("devices", [])]
        groups.setdefault(sid, []).append(SessionPart(d, sid, roles, m))
    return groups


def merge_status(root: str | Path) -> list[dict]:
    """Per session_id: the roles present, whether it's complete, and what's missing."""
    out = []
    for sid, parts in scan_parts(root).items():
        roles = sorted({r for p in parts for r in p.roles})
        missing = [r for r in ("edge", "companion") if r not in roles]
        out.append({
            "session_id": sid,
            "parts": len(parts),
            "roles": roles,
            "complete": not missing,
            "missing": missing,
            "status": "merged" if not missing else "awaiting",
        })
    return out


def _merge_discovery(dst: Path, part_dirs: list[Path]) -> None:
    merged: dict = {"schema_version": SCHEMA}
    for d in part_dirs:
        p = d / "can" / "discovery.json"
        if not p.exists():
            continue
        disc = json.loads(p.read_text(encoding="utf-8"))
        for section in ("obd", "uds", "plain_can"):
            if section in disc:
                merged.setdefault(section, {})
                for k, v in disc[section].items():
                    if isinstance(v, list):
                        merged[section].setdefault(k, [])
                        merged[section][k].extend(v)
                    else:
                        merged[section][k] = v
    if len(merged) > 1:
        (dst / "can").mkdir(parents=True, exist_ok=True)
        (dst / "can" / "discovery.json").write_text(json.dumps(merged, indent=2))


def merge_parts(parts: list[SessionPart], out_dir: str | Path) -> Path:
    """Stitch parts sharing a session_id into one session directory.

    Copies each part's stream files (preserving their relative layout — ``can/``,
    ``edge/``, ``phone/``, ``labels/``), unions the manifest devices and streams,
    and merges ``discovery.json``.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    devices: list[dict] = []
    streams: list[dict] = []
    vehicle: dict | None = None
    created = None
    sid = parts[0].session_id

    for part in parts:
        m = part.manifest
        devices.extend(m.get("devices", []))
        vehicle = vehicle or m.get("vehicle")
        created = created or m.get("created_utc")
        for s in m.get("streams", []):
            rel = s.get("path")
            if not rel:
                continue
            src = part.dir / rel
            if src.exists():
                dst = out / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                streams.append(s)
        # copy any label streams that aren't in the manifest streams list
        labels = part.dir / "labels"
        if labels.is_dir():
            for f in labels.iterdir():
                if f.is_file():
                    (out / "labels").mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, out / "labels" / f.name)

    _merge_discovery(out, [p.dir for p in parts])
    if (out / "can" / "discovery.json").exists() and \
            not any(s.get("path") == "can/discovery.json" for s in streams):
        streams.append({"path": "can/discovery.json", "kind": "discovery"})

    manifest = {
        "schema_version": SCHEMA, "session_id": sid,
        "created_utc": created or 0.0, "devices": devices, "streams": streams,
    }
    if vehicle:
        manifest["vehicle"] = vehicle
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return out


def merge_all(root: str | Path, out_root: str | Path) -> list[Path]:
    """Merge every *complete* (edge+companion) group under ``root`` into ``out_root``."""
    out_root = Path(out_root)
    merged = []
    for sid, parts in scan_parts(root).items():
        roles = {r for p in parts for r in p.roles}
        if {"edge", "companion"}.issubset(roles):
            merged.append(merge_parts(parts, out_root / sid))
    return merged
