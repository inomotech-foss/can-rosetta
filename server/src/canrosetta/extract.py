"""Stage 3 - candidate extraction.

Each periodic plain-CAN frame is a bag of bits with unknown structure. We turn
every arbitration ID into a set of *candidate signals* by enumerating plausible
bit-field interpretations, then discard the ones that are obviously not physical
signals (constants, message counters, checksums). What survives is a pile of
time series to match against the references in Stage 4.

The enumeration is deliberately exhaustive-but-bounded: contiguous 1-4 byte
fields at every offset, both byte orders, signed and unsigned, plus single bits
for flags. A typical 8-byte frame yields a few dozen candidates.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .session import FramesForId, TimeSeries


@dataclass(frozen=True)
class Candidate:
    """A hypothesized bit-field within one arbitration ID."""

    arb_id: int
    byte_offset: int
    width_bytes: int  # 0 => single-bit candidate (see bit_index)
    endian: str  # "big" | "little" | "" for bits
    signed: bool
    bit_index: int = -1  # for single-bit candidates, absolute bit 0..(8*W-1)
    kind: str = "continuous"  # "continuous" | "event"

    @property
    def label(self) -> str:
        if self.width_bytes == 0:
            return f"0x{self.arb_id:X}#bit{self.bit_index}"
        e = "BE" if self.endian == "big" else "LE"
        s = "s" if self.signed else "u"
        end = self.byte_offset + self.width_bytes
        return f"0x{self.arb_id:X}[{self.byte_offset}:{end}]{e}{s}"


def _decode_int(payload: np.ndarray, off: int, w: int, endian: str, signed: bool) -> np.ndarray:
    """Vectorized decode of a w-byte field to int64 over all frames."""
    val = np.zeros(payload.shape[0], dtype=np.int64)
    if endian == "big":
        for k in range(w):
            val = val * 256 + payload[:, off + k].astype(np.int64)
    else:
        for k in range(w):
            val += payload[:, off + k].astype(np.int64) << (8 * k)
    if signed:
        half = 1 << (8 * w - 1)
        full = 1 << (8 * w)
        val = np.where(val >= half, val - full, val)
    return val


def _looks_like_counter(v: np.ndarray) -> bool:
    """A field that increments by a constant step (mod range) is a message counter."""
    if len(v) < 8:
        return False
    d = np.diff(v)
    # ignore wrap-around jumps, then check the rest is a single repeated step
    nonwrap = d[np.abs(d) < (0.5 * (v.max() - v.min() + 1))]
    if len(nonwrap) < 4:
        return False
    step = np.median(nonwrap)
    return step != 0 and np.mean(np.abs(nonwrap - step) < 1e-9) > 0.9


def _looks_like_checksum(v: np.ndarray, byte_range: int) -> bool:
    """High-entropy field with no temporal structure ~ a CRC/checksum byte."""
    if len(v) < 16 or byte_range <= 0:
        return False
    uniq_frac = len(np.unique(v)) / len(v)
    # lag-1 autocorrelation of a real signal is high; a checksum's is ~0
    a = v[:-1].astype(np.float64)
    b = v[1:].astype(np.float64)
    if np.std(a) < 1e-9 or np.std(b) < 1e-9:
        return False
    ac1 = abs(float(np.corrcoef(a, b)[0, 1]))
    return uniq_frac > 0.6 and ac1 < 0.2


def _informative(v: np.ndarray) -> bool:
    return np.ptp(v) > 0  # not constant


def extract_candidates(
    fid: FramesForId,
    *,
    max_width: int = 4,
    include_bits: bool = True,
    drop_counters: bool = True,
    drop_checksums: bool = True,
) -> list[tuple[Candidate, TimeSeries]]:
    """Enumerate candidate signals for one arbitration ID.

    Returns ``(Candidate, TimeSeries)`` pairs; the series' time base is the edge
    clock (callers shift it by the alignment delta before matching).
    """
    out: list[tuple[Candidate, TimeSeries]] = []
    W = fid.width
    payload = fid.payload
    t = fid.t

    for w in range(1, min(max_width, W) + 1):
        for off in range(0, W - w + 1):
            for endian in (("big", "little") if w > 1 else ("big",)):
                for signed in (False, True):
                    v = _decode_int(payload, off, w, endian, signed)
                    if not _informative(v):
                        continue
                    if drop_counters and _looks_like_counter(v):
                        continue
                    if drop_checksums and w == 1 and _looks_like_checksum(v, int(np.ptp(v))):
                        continue
                    cand = Candidate(fid.arb_id, off, w, endian, signed)
                    out.append((cand, TimeSeries(cand.label, t, v.astype(np.float64))))

    if include_bits:
        bits = np.unpackbits(payload, axis=1, bitorder="big")  # [m, 8*W]
        for b in range(bits.shape[1]):
            col = bits[:, b]
            if col.min() == col.max():
                continue  # constant bit
            # skip bits that toggle nearly every frame (counter LSB / noise)
            toggles = np.mean(np.abs(np.diff(col))) if len(col) > 1 else 0.0
            if toggles > 0.45:
                continue
            cand = Candidate(fid.arb_id, b // 8, 0, "", False, bit_index=b, kind="event")
            out.append((cand, TimeSeries(cand.label, t, col.astype(np.float64), kind="event")))

    return out


def extract_session(
    frames_by_id: dict[int, FramesForId], **kw
) -> list[tuple[Candidate, TimeSeries]]:
    """Extract candidates across every arbitration ID in a session."""
    out: list[tuple[Candidate, TimeSeries]] = []
    for fid in frames_by_id.values():
        if len(fid.t) < 8:
            continue
        out.extend(extract_candidates(fid, **kw))
    return out
