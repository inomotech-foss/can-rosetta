"""Behavioral fingerprints for arbitration IDs.

A fingerprint is a fixed-length feature vector describing *how a frame behaves*
independently of what it means: how often it's sent, which bytes move, how
random each byte is, whether bytes look like counters or checksums. This is the
input representation the learned classifier uses to guess a signal's *type* on a
brand-new vehicle before any reference correlation exists — and it's a cheap
pre-filter for the classical baseline.

Pure numpy; no training required to compute one.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..session import FramesForId


@dataclass
class Fingerprint:
    arb_id: int
    period_s: float
    n_frames: int
    per_byte_entropy: np.ndarray  # [width] bits, 0..8
    per_byte_change_rate: np.ndarray  # [width] fraction of frames a byte changes
    counter_byte: int  # index of a detected counter byte, else -1
    checksum_byte: int  # index of a detected checksum byte, else -1

    def vector(self, width: int = 8) -> np.ndarray:
        """Flatten to a fixed-length vector (padded/truncated to ``width`` bytes)."""

        def fit(a: np.ndarray) -> np.ndarray:
            out = np.zeros(width)
            out[: min(width, len(a))] = a[:width]
            return out

        scalars = np.array(
            [
                self.period_s,
                np.log1p(self.n_frames),
                self.counter_byte >= 0,
                self.checksum_byte >= 0,
            ],
            dtype=np.float64,
        )
        return np.concatenate([scalars, fit(self.per_byte_entropy), fit(self.per_byte_change_rate)])


def _byte_entropy(col: np.ndarray) -> float:
    counts = np.bincount(col, minlength=256).astype(np.float64)
    p = counts[counts > 0] / counts.sum()
    return float(-(p * np.log2(p)).sum())


def fingerprint_frame(fid: FramesForId) -> Fingerprint:
    payload = fid.payload
    m, w = payload.shape
    ent = np.array([_byte_entropy(payload[:, j]) for j in range(w)])
    change = (
        np.mean(np.abs(np.diff(payload.astype(np.int16), axis=0)) > 0, axis=0)
        if m > 1
        else np.zeros(w)
    )

    counter_byte = -1
    checksum_byte = -1
    for j in range(w):
        col = payload[:, j].astype(np.int64)
        if m > 8:
            d = np.diff(col)
            nonwrap = d[np.abs(d) < 128]
            if len(nonwrap) > 4 and np.median(nonwrap) != 0:
                if np.mean(np.abs(nonwrap - np.median(nonwrap)) < 1e-9) > 0.9:
                    counter_byte = j
            if ent[j] > 7.0 and change[j] > 0.6 and counter_byte != j:
                checksum_byte = j

    return Fingerprint(
        arb_id=fid.arb_id,
        period_s=fid.period_est,
        n_frames=m,
        per_byte_entropy=ent,
        per_byte_change_rate=change,
        counter_byte=counter_byte,
        checksum_byte=checksum_byte,
    )
