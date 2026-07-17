"""Find *unidentified* structured signals — what to go measure next.

After identification, the interesting leftovers are candidates that are clearly
*real* signals — periodic, structured, and mutually correlated — yet match **no**
reference on this drive. The bus census surfaces them as "meaning unknown": they
are the active-learning targets ("drive again with the fog lights on to
disambiguate 0x4A1"). This module extracts a representative dynamic field per
arbitration ID, drops the ones a reference already explains, and clusters the
rest by mutual correlation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .extract import extract_candidates
from .references import build_references
from .session import Session, TimeSeries
from .signals import common_grid, pearson, resample_uniform
from .taxonomy import DYNAMIC, classify


def cluster_series(series: list[TimeSeries], *, hz: float = 10.0,
                   cluster_r: float = 0.8) -> list[list[str]]:
    """Group series by mutual |correlation| ≥ ``cluster_r`` (union-find).

    Returns clusters (lists of series names) sorted largest-first. A standalone
    series forms its own singleton cluster.
    """
    n = len(series)
    if n == 0:
        return []
    grid = common_grid(*[s.t for s in series], hz=hz)
    if len(grid) < 8:
        return [[s.name] for s in series]
    grids = [resample_uniform(s.t, s.v, grid, max_gap=2.0) for s in series]

    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if abs(pearson(grids[i], grids[j])) >= cluster_r:
                parent[find(i)] = find(j)

    groups: dict[int, list[str]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(series[i].name)
    return sorted(groups.values(), key=len, reverse=True)


def _representatives(session: Session) -> list[TimeSeries]:
    """One representative dynamic field per arbitration ID (the highest-variance one)."""
    reps: list[TimeSeries] = []
    for aid, fid in session.frames.by_id(rx_only=True).items():
        if len(fid.t) < 16:
            continue
        best: TimeSeries | None = None
        best_var = 0.0
        for _cand, ts in extract_candidates(fid, max_width=2, include_bits=False):
            if classify(ts.v).label != DYNAMIC:
                continue
            var = float(np.nanvar(ts.v))
            if var > best_var:
                best_var, best = var, TimeSeries(f"0x{aid:X}", fid.t, ts.v, clock="edge")
        if best is not None:
            reps.append(best)
    return reps


@dataclass
class UnidentifiedResult:
    clusters: list[list[str]] = field(default_factory=list)  # mutually-correlated, unexplained
    explained: list[str] = field(default_factory=list)  # arb IDs a reference accounts for


def unidentified_signals(session: Session, *, hz: float = 10.0,
                         min_ref_r: float = 0.9, cluster_r: float = 0.8,
                         delta: float = 0.0) -> UnidentifiedResult:
    """Cluster structured signals that no reference explains."""
    reps = _representatives(session)
    if not reps:
        return UnidentifiedResult()
    references = build_references(session)

    residual: list[TimeSeries] = []
    explained: list[str] = []
    if references:
        all_t = [r.t for r in references] + [s.t + delta for s in reps]
        grid = common_grid(*all_t, hz=hz)
        ref_grids = [resample_uniform(r.t, r.v, grid, max_gap=2.0) for r in references] \
            if len(grid) >= 8 else []
        for s in reps:
            sv = resample_uniform(s.t + delta, s.v, grid, max_gap=2.0) if ref_grids else None
            max_r = max((abs(pearson(sv, rg)) for rg in ref_grids), default=0.0) \
                if sv is not None else 0.0
            if max_r >= min_ref_r:
                explained.append(s.name)
            else:
                residual.append(s)
    else:
        residual = reps

    clusters = cluster_series(residual, hz=hz, cluster_r=cluster_r)
    return UnidentifiedResult(clusters=clusters, explained=explained)
