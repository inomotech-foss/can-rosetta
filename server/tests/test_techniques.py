"""Tests for techniques adopted from the CAN-RE literature (ByCAN et al.):
banded DTW similarity and the behavioral signal taxonomy.
"""

import numpy as np

from canrosetta.signals import dtw_similarity
from canrosetta.taxonomy import CHECKSUM, CONSTANT, COUNTER, DYNAMIC, SWITCH, classify


def test_dtw_matches_time_warped_copy():
    t = np.linspace(0, 6.28, 200)
    ref = np.sin(t)
    # same shape, resampled at a different (slower, uneven) rate + scaled/offset
    idx = np.linspace(0, len(ref) - 1, 130).astype(int)
    warped = 5.0 * ref[idx] + 2.0
    unrelated = np.random.default_rng(0).normal(size=90)
    assert dtw_similarity(ref, warped) > dtw_similarity(ref, unrelated)
    assert dtw_similarity(ref, warped) > 0.5


def test_dtw_handles_degenerate_input():
    assert dtw_similarity(np.ones(50), np.arange(50)) == 0.0  # constant -> 0
    assert dtw_similarity(np.arange(2), np.arange(2)) == 0.0  # too short


def test_taxonomy_labels():
    n = 300
    assert classify(np.full(n, 7.0)).label == CONSTANT
    assert classify(np.arange(n) & 0xFF).label == COUNTER
    rng = np.random.default_rng(1)
    assert classify(rng.integers(0, 256, n).astype(float)).label == CHECKSUM
    assert classify(rng.integers(0, 3, n).astype(float)).label == SWITCH
    ramp = np.linspace(0, 1000, n) + rng.normal(0, 5, n)
    assert classify(ramp).label == DYNAMIC
