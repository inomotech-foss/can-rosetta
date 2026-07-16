import numpy as np

from canrosetta.model.fingerprint import fingerprint_frame
from canrosetta.model.tokenizer import BOF, EOF, MASK, FrameTokenizer
from canrosetta.session import FramesForId


def test_tokenizer_roundtrip_and_masking():
    tok = FrameTokenizer()
    seq = tok.encode_frame(0x3C0, bytes([0x01, 0x02, 0x03]))
    assert seq[0] == BOF and seq[-1] == EOF
    assert seq[1:3] == [0x03, 0xC0]  # id hi/lo
    assert seq[3:6] == [1, 2, 3]

    rng = np.random.default_rng(0)
    tokens = np.array([seq, seq], dtype=np.int32)
    masked, labels = tok.mask_tokens(tokens, rng, p=1.0)
    # every real byte gets masked at p=1.0; specials never do
    byte_pos = tokens < 256
    assert np.all(masked[byte_pos] == MASK)
    assert np.all(labels[~byte_pos] == -100)
    assert np.all(labels[byte_pos] == tokens[byte_pos])


def test_fingerprint_detects_counter_byte():
    m = 100
    payload = np.zeros((m, 4), dtype=np.uint8)
    payload[:, 0] = np.arange(m) & 0xFF  # counter
    payload[:, 1] = 0x42  # constant
    t = np.arange(m) * 0.02
    fp = fingerprint_frame(FramesForId(0x100, t, t, payload))
    assert fp.counter_byte == 0
    assert fp.per_byte_entropy[1] == 0.0  # constant byte has zero entropy
    assert fp.vector(width=8).shape[0] == 4 + 8 + 8
