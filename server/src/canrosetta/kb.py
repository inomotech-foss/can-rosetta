"""Cross-vehicle signal knowledge base + per-platform rejection memory.

Identification runs per session, but knowledge accrues across the fleet. The KB
persists, per **platform** (e.g. "VW Golf"):

- **confirmed** mappings (reference → CAN field) that become training labels and
  regression fixtures, and
- **rejected** candidate pairs, so a false friend rejected on one drive
  ("MQB false friends") stays rejected on the next car of the same platform.

It also computes **coverage** — how many of a session's dynamic fields we have a
confirmed mapping for — the metric the Knowledge base view reports.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .clusters import _representatives
from .identify import IdentifyResult
from .session import Session


def platform_of(session: Session) -> str:
    v = session.manifest.get("vehicle", {})
    parts = [str(v[k]) for k in ("make", "model") if v.get(k)]
    return " ".join(parts) or "unknown"


def _key(reference: str, candidate_label: str) -> str:
    return f"{reference}||{candidate_label}"


@dataclass
class KnowledgeBase:
    path: Path
    data: dict

    @classmethod
    def load(cls, path: str | Path) -> KnowledgeBase:
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() \
            else {"schema_version": "1.0.0", "platforms": {}}
        return cls(path, data)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2))

    def _platform(self, platform: str) -> dict:
        return self.data["platforms"].setdefault(
            platform, {"confirmed": {}, "rejected": []})

    def confirm(self, platform: str, reference: str, candidate: dict,
                r: float, vehicle: str | None = None) -> None:
        p = self._platform(platform)
        entry = p["confirmed"].get(reference, {"vehicles": []})
        entry.update({"candidate": candidate.get("label", ""), "field": candidate, "r": r})
        if vehicle and vehicle not in entry["vehicles"]:
            entry["vehicles"].append(vehicle)
        p["confirmed"][reference] = entry

    def reject(self, platform: str, reference: str, candidate_label: str) -> None:
        p = self._platform(platform)
        k = _key(reference, candidate_label)
        if k not in p["rejected"]:
            p["rejected"].append(k)

    def is_rejected(self, platform: str, reference: str, candidate_label: str) -> bool:
        p = self.data["platforms"].get(platform)
        return bool(p and _key(reference, candidate_label) in p["rejected"])

    def signals(self, platform: str) -> dict:
        p = self.data["platforms"].get(platform, {})
        return p.get("confirmed", {})

    def summary(self) -> list[dict]:
        out = []
        for name, p in sorted(self.data["platforms"].items()):
            confirmed = p.get("confirmed", {})
            vehicles = sorted({v for e in confirmed.values() for v in e.get("vehicles", [])})
            out.append({"platform": name, "signals": len(confirmed),
                        "vehicles": len(vehicles), "rejected": len(p.get("rejected", []))})
        return out


def apply_rejections(result: IdentifyResult, kb: KnowledgeBase, platform: str) -> IdentifyResult:
    """Drop hypotheses previously rejected for this platform (rejection memory)."""
    for ref, hyps in result.per_reference.items():
        result.per_reference[ref] = [
            h for h in hyps if not kb.is_rejected(platform, ref, h.candidate.label)
        ]
    return result


def write_annotation(session_root: str | Path, reference: str, candidate: dict,
                     r: float) -> None:
    """Append a confirmed mapping to ``labels/annotations.json`` (a training label)."""
    root = Path(session_root)
    ann_path = root / "labels" / "annotations.json"
    ann = json.loads(ann_path.read_text(encoding="utf-8")) if ann_path.exists() \
        else {"schema_version": "1.0.0", "signals": []}
    ann.setdefault("signals", []).append(
        {"reference": reference, "candidate": candidate, "r": r})
    ann_path.parent.mkdir(parents=True, exist_ok=True)
    ann_path.write_text(json.dumps(ann, indent=2))


def coverage(session: Session, result: IdentifyResult, *, min_r: float = 0.9) -> dict:
    """Coverage = dynamic arb-IDs with a confident mapping ÷ dynamic arb-IDs observed."""
    dynamic_ids = {int(s.name, 16) for s in _representatives(session)}
    covered = {h.candidate.arb_id for h in result.confident(min_r=min_r)}
    covered_dyn = covered & dynamic_ids
    total = len(dynamic_ids)
    return {
        "dynamic_fields": total,
        "confirmed_fields": len(covered_dyn),
        "coverage": (len(covered_dyn) / total) if total else 0.0,
    }
