"""Stage 4 - signal identification.

Match every extracted CAN candidate against every reference signal and rank the
relationships. A candidate that is an affine transform of GPS speed *is* the
speed signal; the least-squares fit hands back the scale and offset (the DBC
factor and offset). The result is, per reference, a ranked list of hypotheses
plus a set of high-confidence mappings ready to export as a DBC.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

from .align import Alignment, estimate_alignment
from .extract import Candidate, extract_session
from .references import build_references
from .session import Session, TimeSeries
from .signals import common_grid, linfit, mutual_information, pearson, resample_uniform


@dataclass
class Hypothesis:
    reference: str
    candidate: Candidate
    r: float  # correlation (signed)
    scale: float  # reference ≈ scale*raw + offset
    offset: float
    mutual_info: float
    n: int  # jointly-finite samples the score is based on

    def as_dict(self) -> dict:
        d = {"reference": self.reference, "candidate": self.candidate.label}
        d.update({k: getattr(self, k) for k in ("r", "scale", "offset", "mutual_info", "n")})
        d["field"] = asdict(self.candidate)
        return d


@dataclass
class IdentifyResult:
    session_id: str
    alignment: Alignment
    per_reference: dict[str, list[Hypothesis]] = field(default_factory=dict)
    n_candidates: int = 0

    def confident(self, min_r: float = 0.9) -> list[Hypothesis]:
        """Best hypothesis per reference whose |r| clears ``min_r``."""
        out = []
        for hyps in self.per_reference.values():
            if hyps and abs(hyps[0].r) >= min_r:
                out.append(hyps[0])
        return out

    def as_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "alignment": asdict(self.alignment),
            "n_candidates": self.n_candidates,
            "per_reference": {
                ref: [h.as_dict() for h in hyps] for ref, hyps in self.per_reference.items()
            },
        }


def identify_session(
    session: Session,
    *,
    hz: float = 10.0,
    top_k: int = 5,
    min_overlap: int = 20,
    alignment: Alignment | None = None,
) -> IdentifyResult:
    """Run alignment + extraction + matching and return ranked hypotheses."""
    align = alignment or estimate_alignment(session)
    delta = align.delta

    references = build_references(session)
    candidates = extract_session(session.frames.by_id(rx_only=True))

    result = IdentifyResult(
        session_id=session.session_id, alignment=align, n_candidates=len(candidates)
    )
    if not references or not candidates:
        return result

    # everything is placed on the companion clock: edge-sourced series (all
    # candidates, plus OBD/UDS references) are shifted by delta; phone-sourced
    # references (GPS/IMU) are already there.
    def on_companion(ts: TimeSeries) -> np.ndarray:
        return ts.t + delta if ts.clock == "edge" else ts.t

    all_t = [on_companion(r) for r in references] + [c[1].t + delta for c in candidates]
    grid = common_grid(*all_t, hz=hz)
    if len(grid) < min_overlap:
        return result

    # resample everything once onto the grid, then correlate pairwise
    ref_grid = {
        r.name: (r, resample_uniform(on_companion(r), r.v, grid, max_gap=2.0))
        for r in references
    }
    cand_grid = [
        (cand, ts, resample_uniform(ts.t + delta, ts.v, grid, max_gap=2.0))
        for cand, ts in candidates
    ]

    for ref_name, (_ref_ts, ref_vals) in ref_grid.items():
        scored: list[Hypothesis] = []
        for cand, _ts, cvals in cand_grid:
            m = np.isfinite(ref_vals) & np.isfinite(cvals)
            n = int(m.sum())
            if n < min_overlap:
                continue
            r = pearson(ref_vals[m], cvals[m])
            if abs(r) < 0.3:
                continue
            scale, offset, _ = linfit(cvals[m], ref_vals[m])
            mi = mutual_information(ref_vals[m], cvals[m]) if abs(r) > 0.5 else 0.0
            scored.append(Hypothesis(ref_name, cand, r, scale, offset, mi, n))

        scored.sort(key=lambda h: (abs(h.r), h.mutual_info), reverse=True)
        _apply_priors(ref_name, scored)
        if scored:
            result.per_reference[ref_name] = scored[:top_k]

    return result


def _apply_priors(ref_name: str, scored: list[Hypothesis]) -> None:
    """Light physical priors: nudge implausible top hits down a rank.

    Speed and RPM are non-negative and increase with the reference, so a strongly
    *negative* correlation for those is more likely a coincidence than the true
    signal. We don't drop it, just prefer a positive-slope alternative of similar
    strength.
    """
    nonneg = any(k in ref_name for k in ("speed", "rpm", "accel_long"))
    if not nonneg or len(scored) < 2:
        return
    scored.sort(
        key=lambda h: (round(abs(h.r), 3), h.r > 0, h.mutual_info),
        reverse=True,
    )
