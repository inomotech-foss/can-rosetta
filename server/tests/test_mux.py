"""Multiplexed-frame detection and per-selector extraction."""

from __future__ import annotations

import numpy as np

from canrosetta.mux import detect_multiplexor, extract_multiplexed
from canrosetta.session import FramesForId
from canrosetta.signals import pearson


def _muxed_fid(m: int = 300) -> tuple[FramesForId, dict[int, np.ndarray]]:
    """Frame 0x5C0: byte0 = selector cycling 0,1,2; bytes1-2 = a per-selector signal."""
    payload = np.zeros((m, 8), dtype=np.uint8)
    sel = np.arange(m) % 3
    payload[:, 0] = sel
    # three distinct slow signals, one per selector value
    sig = {
        0: (2000 + 500 * np.sin(np.linspace(0, 6, m))),   # e.g. coolant
        1: (1000 + 300 * np.cos(np.linspace(0, 4, m))),   # e.g. oil
        2: (3000 + 800 * np.sin(np.linspace(0, 9, m))),   # e.g. battery temp
    }
    truth = {}
    for v in (0, 1, 2):
        raw = sig[v].astype(np.uint16)
        payload[sel == v, 1] = (raw[sel == v] >> 8) & 0xFF
        payload[sel == v, 2] = raw[sel == v] & 0xFF
        truth[v] = sig[v][sel == v]
    t = np.arange(m) * 0.05
    return FramesForId(0x5C0, t=t, t_mono=t, payload=payload), truth


def test_detects_selector_byte():
    fid, _ = _muxed_fid()
    mux = detect_multiplexor(fid)
    assert mux is not None
    assert mux.byte_offset == 0
    assert set(mux.values) == {0, 1, 2}
    assert mux.score > 0.5  # selector explains most of the muxed bytes' variance


def test_extracts_per_selector_signals():
    fid, truth = _muxed_fid()
    mux = detect_multiplexor(fid)
    cands = extract_multiplexed(fid, mux)
    # for each selector value there is a [1:3] big-endian candidate matching truth
    for v in (0, 1, 2):
        matches = [
            (c, ts) for c, ts in cands
            if c.mux_value == v and c.byte_offset == 1 and c.width_bytes == 2 and c.endian == "big"
        ]
        assert matches, f"no [1:3]BE candidate for selector {v}"
        _, ts = matches[0]
        assert pearson(ts.v, truth[v]) > 0.98
        assert "m0=" in matches[0][0].label


def test_non_multiplexed_frame_returns_none():
    m = 200
    payload = np.zeros((m, 8), dtype=np.uint8)
    payload[:, 1] = (np.linspace(0, 60000, m)).astype(np.uint16) >> 8  # plain ramp, no selector
    t = np.arange(m) * 0.02
    assert detect_multiplexor(FramesForId(0x100, t=t, t_mono=t, payload=payload)) is None
