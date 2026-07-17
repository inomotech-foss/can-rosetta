"""Multiplexed-frame handling.

Real buses reuse payload bytes: a frame carries a small **multiplexor** selector
(a byte or nibble) whose value decides what the *rest* of the bytes mean. The same
bytes are coolant temp when the selector is 0, oil temp when it's 1, and so on.
A plain per-frame extractor mixes those together and finds nothing; you have to
split the frames by selector value first.

Detection is unsupervised: a byte is a multiplexor if it has low cardinality and
**conditioning on it collapses the entropy** of other bytes — i.e. once you know
the selector, the other bytes become predictable. We then extract candidates
*within each selector value* over just the frames carrying it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .extract import Candidate, _decode_int
from .session import FramesForId, TimeSeries


def _eta_squared(values: np.ndarray, groups: np.ndarray, group_vals: np.ndarray) -> float:
    """Correlation ratio η² in [0,1]: fraction of ``values`` variance explained by group.

    High η² means knowing the group pins down the value's range — exactly what a
    multiplexor does to the bytes it selects (each muxed signal lives in its own
    value band).
    """
    x = values.astype(np.float64)
    total_ss = float(np.sum((x - x.mean()) ** 2))
    if total_ss < 1e-9:
        return 0.0
    between = 0.0
    for v in group_vals:
        sub = x[groups == v]
        if len(sub):
            between += len(sub) * (sub.mean() - x.mean()) ** 2
    return float(between / total_ss)


@dataclass
class Multiplexor:
    byte_offset: int
    values: list[int]  # observed selector values
    score: float  # best correlation ratio (η²) of a conditioned byte, in [0,1]


def detect_multiplexor(fid: FramesForId, *, max_values: int = 16,
                       min_score: float = 0.5) -> Multiplexor | None:
    """Find the most likely multiplexor byte, or None.

    A byte is a multiplexor if it has low cardinality and **explains** the value
    range of other bytes: ``score`` is the mean η² (correlation ratio) of the
    other, non-constant bytes when grouped by the selector. This works for
    continuous muxed signals (which keep varying within a selector value but sit
    in a selector-dependent band), where an entropy-collapse test would fail.
    """
    payload = fid.payload
    m, w = payload.shape
    if m < 32 or w < 2:
        return None

    best: Multiplexor | None = None
    for s in range(w):
        sel = payload[:, s]
        vals = np.unique(sel)
        if not (2 <= len(vals) <= max_values):
            continue
        etas = []
        for b in range(w):
            if b == s:
                continue
            if np.ptp(payload[:, b]) == 0:
                continue  # constant byte carries no info
            etas.append(_eta_squared(payload[:, b], sel, vals))
        if not etas:
            continue
        # a multiplexor need only strongly explain SOME byte (its muxed MSB); the
        # co-located LSB stays near-uniform, so max (not mean) is the right score.
        score = float(np.max(etas))
        if score >= min_score and (best is None or score > best.score):
            best = Multiplexor(s, [int(v) for v in vals], score)
    return best


@dataclass(frozen=True)
class MuxCandidate:
    """A candidate field valid only when the multiplexor equals ``mux_value``."""

    arb_id: int
    mux_byte: int
    mux_value: int
    byte_offset: int
    width_bytes: int
    endian: str
    signed: bool

    @property
    def label(self) -> str:
        e = "BE" if self.endian == "big" else "LE"
        s = "s" if self.signed else "u"
        end = self.byte_offset + self.width_bytes
        return (f"0x{self.arb_id:X}[m{self.mux_byte}={self.mux_value}]"
                f"[{self.byte_offset}:{end}]{e}{s}")


def extract_multiplexed(fid: FramesForId, mux: Multiplexor, *,
                        max_width: int = 2) -> list[tuple[MuxCandidate, TimeSeries]]:
    """Extract candidates within each selector value (skipping the selector byte).

    Each returned series spans only the frames whose selector equals that value,
    so a signal multiplexed behind the selector becomes its own time series.
    """
    payload = fid.payload
    t = fid.t
    W = fid.width
    sel = payload[:, mux.byte_offset]
    out: list[tuple[MuxCandidate, TimeSeries]] = []

    for v in mux.values:
        rows = sel == v
        if rows.sum() < 8:
            continue
        sub = payload[rows]
        sub_t = t[rows]
        for width in range(1, min(max_width, W) + 1):
            for off in range(0, W - width + 1):
                if off <= mux.byte_offset < off + width:
                    continue  # don't mine the selector byte itself
                for endian in (("big", "little") if width > 1 else ("big",)):
                    for signed in (False, True):
                        vals = _decode_int(sub, off, width, endian, signed).astype(np.float64)
                        if np.ptp(vals) == 0:
                            continue
                        c = MuxCandidate(fid.arb_id, mux.byte_offset, int(v),
                                         off, width, endian, signed)
                        out.append((c, TimeSeries(c.label, sub_t, vals)))
    return out


def to_plain_candidate(mc: MuxCandidate) -> Candidate:
    """Adapt a MuxCandidate to a plain Candidate (for DBC export / uniform handling)."""
    return Candidate(mc.arb_id, mc.byte_offset, mc.width_bytes, mc.endian, mc.signed)
