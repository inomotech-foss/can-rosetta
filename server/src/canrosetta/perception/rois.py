"""Regions of interest on the dashboard video.

A vehicle's dashboard layout is fixed for a session, so the operator marks the
relevant regions once (or a detector proposes them) and stores them in a
``perception.json`` alongside the video. Each ROI names a dashboard element, the
pixel box to read, the extractor kind, and any calibration that kind needs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# extractor kinds
DIGITS = "digits"
TELLTALE = "telltale"
NEEDLE = "needle"
GEAR = "gear"


@dataclass
class ROI:
    name: str  # signal-style name, e.g. "dash_speed", "turn_left", "gear"
    kind: str  # DIGITS | TELLTALE | NEEDLE | GEAR
    box: tuple[int, int, int, int]  # (x, y, w, h) in pixels
    params: dict = field(default_factory=dict)  # calibration per kind

    def crop(self, frame):
        x, y, w, h = self.box
        return frame[y:y + h, x:x + w]


@dataclass
class ROISet:
    rois: list[ROI]

    @classmethod
    def from_json(cls, path: str | Path) -> ROISet:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> ROISet:
        rois = [
            ROI(
                name=r["name"],
                kind=r["kind"],
                box=tuple(r["box"]),  # type: ignore[arg-type]
                params=r.get("params", {}),
            )
            for r in data.get("rois", [])
        ]
        return cls(rois)

    def to_dict(self) -> dict:
        return {"rois": [{"name": r.name, "kind": r.kind, "box": list(r.box),
                          "params": r.params} for r in self.rois]}
