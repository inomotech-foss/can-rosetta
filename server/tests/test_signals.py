import numpy as np

from canrosetta.signals import best_lag, common_grid, linfit, pearson, resample_uniform


def test_pearson_perfect_and_degenerate():
    x = np.arange(100, dtype=float)
    assert pearson(x, 2 * x + 5) > 0.999
    assert pearson(x, np.full_like(x, 3.0)) == 0.0  # constant -> 0


def test_linfit_recovers_scale_offset():
    x = np.linspace(0, 500, 200)
    y = 0.01 * x - 2.0
    scale, offset, r = linfit(x, y)
    assert abs(scale - 0.01) < 1e-6
    assert abs(offset + 2.0) < 1e-6
    assert r > 0.999


def test_best_lag_finds_shift():
    hz = 10.0
    t = np.arange(0, 30, 1 / hz)
    ref = np.sin(t)
    shift = 7  # sig is ref delayed by 7 samples => 0.7 s
    sig = np.roll(ref, shift)
    lag, r = best_lag(ref, sig, hz=hz, max_lag_s=2.0)
    # convention: sig(t) ≈ ref(t + lag); a +0.7s delay yields lag = -0.7
    assert abs(lag - (-0.7)) < 1e-6
    assert abs(r) > 0.99


def test_resample_respects_max_gap():
    t = np.array([0.0, 1.0, 5.0])
    v = np.array([0.0, 1.0, 5.0])
    grid = np.array([0.5, 3.0])
    out = resample_uniform(t, v, grid, max_gap=2.0)
    assert abs(out[0] - 0.5) < 1e-9  # inside a small gap -> interpolated
    assert np.isnan(out[1])  # inside the 4s gap -> NaN, not invented


def test_common_grid_overlap():
    a = np.array([0.0, 10.0])
    b = np.array([5.0, 20.0])
    grid = common_grid(a, b, hz=1.0)
    assert grid[0] == 5.0 and grid[-1] == 10.0
