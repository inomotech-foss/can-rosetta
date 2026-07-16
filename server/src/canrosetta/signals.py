"""Numpy-only signal-processing helpers.

Kept dependency-free (just numpy) on purpose: the identification baseline must
run anywhere, including on an edge box or in CI without scipy/sklearn. Everything
here operates on irregularly-sampled ``(t, v)`` series and resamples onto a
shared uniform grid when a computation needs aligned samples.
"""

from __future__ import annotations

import numpy as np


def resample_uniform(
    t: np.ndarray, v: np.ndarray, grid: np.ndarray, *, max_gap: float | None = None
) -> np.ndarray:
    """Linearly resample ``(t, v)`` onto ``grid``.

    Samples on ``grid`` that fall in a gap of the source series longer than
    ``max_gap`` (if given) are set to NaN, so we never invent data across a
    dropout. ``t`` must be sorted ascending.
    """
    t = np.asarray(t, dtype=np.float64)
    v = np.asarray(v, dtype=np.float64)
    grid = np.asarray(grid, dtype=np.float64)
    if len(t) == 0:
        return np.full(grid.shape, np.nan)
    out = np.interp(grid, t, v, left=np.nan, right=np.nan)
    if max_gap is not None and len(t) > 1:
        idx = np.searchsorted(t, grid) - 1
        idx = np.clip(idx, 0, len(t) - 2)
        gap = t[idx + 1] - t[idx]
        out[gap > max_gap] = np.nan
    return out


def common_grid(*series_t: np.ndarray, hz: float) -> np.ndarray:
    """Build a uniform time grid at ``hz`` covering the overlap of all inputs."""
    starts = [float(t[0]) for t in series_t if len(t)]
    ends = [float(t[-1]) for t in series_t if len(t)]
    if not starts:
        return np.empty(0)
    t0, t1 = max(starts), min(ends)
    if t1 <= t0:
        return np.empty(0)
    n = int((t1 - t0) * hz) + 1
    return t0 + np.arange(n) / hz


def _finite_mask(*arrs: np.ndarray) -> np.ndarray:
    m = np.ones(arrs[0].shape, dtype=bool)
    for a in arrs:
        m &= np.isfinite(a)
    return m


def pearson(a: np.ndarray, b: np.ndarray) -> float:
    """Pearson correlation over the jointly-finite samples (0 if degenerate)."""
    m = _finite_mask(a, b)
    if m.sum() < 3:
        return 0.0
    a2, b2 = a[m], b[m]
    if np.std(a2) < 1e-12 or np.std(b2) < 1e-12:
        return 0.0
    return float(np.corrcoef(a2, b2)[0, 1])


def best_lag(
    ref: np.ndarray, sig: np.ndarray, hz: float, max_lag_s: float
) -> tuple[float, float]:
    """Find the lag (seconds) that maximizes |correlation| of ``sig`` vs ``ref``.

    Both inputs are assumed already sampled on the same uniform grid at ``hz``.
    Returns ``(lag, correlation_at_that_lag)`` where ``lag`` is defined so that
    ``sig(t) ≈ ref(t + lag)`` — i.e. if ``sig`` is a copy of ``ref`` delayed by
    ``tau`` seconds, the returned lag is ``-tau``. This is the convention the
    aligner needs to map the edge clock onto the companion clock.
    """
    max_k = int(round(max_lag_s * hz))
    best_k, best_abs, best_r = 0, -1.0, 0.0
    for k in range(-max_k, max_k + 1):
        if k >= 0:
            r = pearson(ref[k:], sig[: len(sig) - k]) if k < len(sig) else 0.0
        else:
            r = pearson(ref[: len(ref) + k], sig[-k:])
        if abs(r) > best_abs:
            best_abs, best_r, best_k = abs(r), r, k
    return best_k / hz, float(best_r)


def linfit(x: np.ndarray, y: np.ndarray) -> tuple[float, float, float]:
    """Least-squares fit ``y ≈ scale*x + offset``.

    Returns ``(scale, offset, r)``. This is how a raw CAN candidate's units are
    recovered: fit it against the physical reference and read off the DBC factor
    (scale) and offset.
    """
    m = _finite_mask(x, y)
    if m.sum() < 3 or np.std(x[m]) < 1e-12:
        return 0.0, 0.0, 0.0
    x2, y2 = x[m], y[m]
    A = np.vstack([x2, np.ones_like(x2)]).T
    (scale, offset), *_ = np.linalg.lstsq(A, y2, rcond=None)
    return float(scale), float(offset), pearson(x2, y2)


def mutual_information(a: np.ndarray, b: np.ndarray, bins: int = 16) -> float:
    """Mutual information (nats) between two continuous series via histogram.

    Catches monotone-but-nonlinear relationships that Pearson misses. Not used as
    the primary score but as a tie-breaker / nonlinearity flag.
    """
    m = _finite_mask(a, b)
    if m.sum() < bins * 2:
        return 0.0
    a2, b2 = a[m], b[m]
    if np.std(a2) < 1e-12 or np.std(b2) < 1e-12:
        return 0.0
    c, _, _ = np.histogram2d(a2, b2, bins=bins)
    pxy = c / c.sum()
    px = pxy.sum(axis=1, keepdims=True)
    py = pxy.sum(axis=0, keepdims=True)
    nz = pxy > 0
    return float(np.sum(pxy[nz] * np.log(pxy[nz] / (px @ py)[nz])))


def derivative(t: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Central-difference derivative dv/dt on an irregular grid."""
    t = np.asarray(t, dtype=np.float64)
    v = np.asarray(v, dtype=np.float64)
    if len(t) < 2:
        return np.zeros_like(v)
    return np.gradient(v, t)
