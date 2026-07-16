"""Classify a decoded field by *how it behaves* — the ByCAN signal taxonomy.

The ByCAN paper (arXiv:2408.09265) labels every CAN field by behavior before
trying to name it, which sharpens both boundary detection and matching. We adopt
the same taxonomy as a light, unsupervised pre-filter: it tells the identifier
which candidates are even worth correlating (dynamic/continuous ones) and lets us
drop the rest (constants, counters, checksums) up front.

Pure numpy; no training. Features mirror ByCAN's: flip rate, distinct-value
ratio, and their product.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# labels
CONSTANT = "constant"  # never changes (ByCAN "unused")
SWITCH = "switch"  # few discrete levels — flags, gear, mode
COUNTER = "counter"  # increments by a fixed step (message counter)
CHECKSUM = "checksum"  # changes almost every frame, high entropy (CRC/verification)
DYNAMIC = "dynamic"  # continuously varying physical quantity — the ones we correlate


@dataclass
class FieldStats:
    label: str
    flip_rate: float  # fraction of consecutive frames the value changes
    distinct_ratio: float  # distinct values / n
    entropy_bits: float
    theta: float  # flip_rate * distinct_ratio (ByCAN labeling parameter)


def _entropy_bits(v: np.ndarray) -> float:
    _, counts = np.unique(v, return_counts=True)
    p = counts / counts.sum()
    return float(-(p * np.log2(p)).sum())


def _is_counter(v: np.ndarray) -> bool:
    if len(v) < 8:
        return False
    d = np.diff(v.astype(np.int64))
    rng = float(np.ptp(v)) + 1.0
    nonwrap = d[np.abs(d) < 0.5 * rng]
    if len(nonwrap) < 4:
        return False
    step = np.median(nonwrap)
    return step != 0 and float(np.mean(np.abs(nonwrap - step) < 1e-9)) > 0.9


def classify(values: np.ndarray, *, switch_max_levels: int = 8) -> FieldStats:
    """Label one decoded field's time series."""
    v = np.asarray(values, dtype=np.float64)
    v = v[np.isfinite(v)]
    n = len(v)
    if n == 0 or np.ptp(v) == 0:
        return FieldStats(CONSTANT, 0.0, 0.0, 0.0, 0.0)

    flip_rate = float(np.mean(np.diff(v) != 0)) if n > 1 else 0.0
    distinct = len(np.unique(v))
    distinct_ratio = distinct / n
    entropy = _entropy_bits(v)
    theta = flip_rate * distinct_ratio

    # lag-1 autocorrelation separates a checksum (no temporal structure, ~0) from
    # a real signal that also changes every frame (smooth, high autocorrelation).
    if n > 2 and np.std(v[:-1]) > 1e-12 and np.std(v[1:]) > 1e-12:
        ac1 = abs(float(np.corrcoef(v[:-1], v[1:])[0, 1]))
    else:
        ac1 = 0.0

    if _is_counter(v):
        label = COUNTER
    elif flip_rate >= 0.99 and entropy > 6.0 and ac1 < 0.3:
        label = CHECKSUM
    elif distinct <= switch_max_levels:
        label = SWITCH
    else:
        label = DYNAMIC
    return FieldStats(label, flip_rate, distinct_ratio, entropy, theta)
