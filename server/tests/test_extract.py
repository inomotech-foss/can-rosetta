import numpy as np

from canrosetta.extract import _decode_int, extract_candidates
from canrosetta.session import FramesForId


def _fid(payload: np.ndarray) -> FramesForId:
    m = payload.shape[0]
    t = np.arange(m) * 0.02
    return FramesForId(arb_id=0x100, t=t, t_mono=t, payload=payload.astype(np.uint8))


def test_decode_big_endian_unsigned():
    payload = np.array([[0x01, 0x02], [0xFF, 0xFF]], dtype=np.uint8)
    v = _decode_int(payload, 0, 2, "big", False)
    assert list(v) == [0x0102, 0xFFFF]


def test_decode_little_endian_and_signed():
    payload = np.array([[0x00, 0x80]], dtype=np.uint8)  # LE = 0x8000
    assert _decode_int(payload, 0, 2, "little", False)[0] == 0x8000
    assert _decode_int(payload, 0, 2, "little", True)[0] == -0x8000


def test_extractor_finds_ramp_and_rejects_counter_and_constant():
    m = 200
    ramp = (np.linspace(0, 60000, m)).astype(np.uint16)
    payload = np.zeros((m, 8), dtype=np.uint8)
    payload[:, 0] = np.arange(m) & 0xFF  # counter byte -> reject
    payload[:, 1] = (ramp >> 8) & 0xFF  # signal MSB
    payload[:, 2] = ramp & 0xFF  # signal LSB
    payload[:, 3] = 0x00  # constant -> reject
    cands = extract_candidates(_fid(payload))
    labels = {c.label for c, _ in cands}

    # the real 2-byte big-endian signal at offset 1 must be present
    assert "0x100[1:3]BEu" in labels
    # the pure counter byte on its own should be dropped
    assert "0x100[0:1]BEu" not in labels
    # the constant byte must be dropped
    assert "0x100[3:4]BEu" not in labels
