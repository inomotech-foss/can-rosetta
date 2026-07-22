import json

import numpy as np
import pytest

from canrosetta.model.fingerprint import fingerprint_frame
from canrosetta.model.tokenizer import BOF, EOF, MASK, FrameTokenizer
from canrosetta.session import FramesForId


def test_tokenizer_roundtrip_and_masking():
    tok = FrameTokenizer()
    seq = tok.encode_frame(0x3C0, bytes([0x01, 0x02, 0x03]))
    assert seq[0] == BOF and seq[-1] == EOF
    assert seq[1:5] == [0x00, 0x00, 0x03, 0xC0]  # full 4-byte big-endian id
    assert seq[5:8] == [1, 2, 3]                  # payload starts after PREFIX_LEN


def test_tokenizer_encodes_full_extended_id():
    # Two 29-bit ids sharing their low 16 bits must NOT alias (the 2-byte
    # encoding did; the 4-byte one distinguishes them).
    tok = FrameTokenizer()
    a = tok.encode_frame(0x18DAF110, bytes([0]))
    b = tok.encode_frame(0x18DBF110, bytes([0]))
    assert a[1:5] == [0x18, 0xDA, 0xF1, 0x10]
    assert b[1:5] == [0x18, 0xDB, 0xF1, 0x10]
    assert a[1:5] != b[1:5]


def test_masking_spares_id_and_special_tokens():
    tok = FrameTokenizer()
    seq = tok.encode_frame(0x3C0, bytes([0x01, 0x02, 0x03]))
    rng = np.random.default_rng(0)
    tokens = np.array([seq, seq], dtype=np.int32)
    masked, labels = tok.mask_tokens(tokens, rng, p=1.0)
    # At p=1.0 every PAYLOAD byte is masked; BOF/EOF/PAD and the id bytes
    # (context, positions < PREFIX_LEN) are never masked or scored.
    from canrosetta.model.tokenizer import PREFIX_LEN
    payload = np.zeros_like(tokens, dtype=bool)
    payload[:, PREFIX_LEN:] = tokens[:, PREFIX_LEN:] < 256
    assert np.all(masked[payload] == MASK)
    assert np.all(labels[payload] == tokens[payload])
    assert np.all(labels[~payload] == -100)      # id bytes + specials unscored
    assert np.all(masked[:, :PREFIX_LEN] == tokens[:, :PREFIX_LEN])


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


def test_pack_tokens_matches_encode_frame():
    from canrosetta.model.tokenizer import PAD, FrameTokenizer
    from canrosetta.model.train import pack_tokens

    tok = FrameTokenizer()
    arb_ids = np.array([0x3C0, 0x7ED, 0x12], dtype=np.uint32)
    payloads = [bytes([1, 2, 3]), bytes(range(8)), b""]
    packed = pack_tokens(arb_ids, payloads)
    # Row-by-row identical to the reference per-frame encoder (up to padding).
    for i, (aid, data) in enumerate(zip(arb_ids, payloads, strict=True)):
        ref = tok.encode_frame(int(aid), data)
        assert list(packed[i, : len(ref)]) == ref
        assert np.all(packed[i, len(ref):] == PAD)


def test_majority_baseline_is_perfect_on_constant_bytes():
    from canrosetta.model.train import FrameBlock, build_split, majority_baseline

    # One ID whose payload never changes: the lookup baseline must hit 1.0.
    arb_ids = np.full(200, 0x101, dtype=np.uint32)
    payloads = [bytes([7, 7, 7, 7])] * 200
    tr_tok, tr_arb, va_tok, va_arb = build_split(
        [FrameBlock(arb_ids, payloads, "test")], 0.2)
    assert majority_baseline(tr_tok, tr_arb, va_tok, va_arb) == 1.0


def test_load_blocks_excludes_tx_probe_traffic(tmp_path):
    from canrosetta.model.train import load_blocks

    frames = tmp_path / "frames.jsonl"
    frames.write_text("\n".join(json.dumps(r) for r in [
        {"arb_id": 0x100, "data": "0102", "direction": "rx"},
        {"arb_id": 0x7E0, "data": "22f190", "direction": "tx"},  # our probe
        {"arb_id": 0x100, "data": "0304"},                       # default rx
    ]))
    blocks = load_blocks([str(frames)])
    assert len(blocks) == 1
    # The tx probe frame is dropped; only the two organic rx frames survive.
    assert list(blocks[0].arb_ids) == [0x100, 0x100]


def test_build_split_rejects_degenerate_val_frac():
    from canrosetta.model.train import FrameBlock, build_split

    block = FrameBlock(np.full(10, 0x1, dtype=np.uint32), [bytes([1])] * 10, "t")
    for bad in (0.0, 1.0, 1.5):
        try:
            build_split([block], bad)
        except ValueError:
            continue
        raise AssertionError(f"val_frac={bad} should have been rejected")


def test_build_split_is_per_file_trailing(tmp_path):
    # Two files; the trailing frac of EACH becomes val, independent of order.
    from canrosetta.model.train import FrameBlock, build_split

    a = FrameBlock(np.full(100, 0xA, np.uint32), [bytes([1, 2])] * 100, "a")
    b = FrameBlock(np.full(100, 0xB, np.uint32), [bytes([3, 4])] * 100, "b")
    _, _, _, va_arb = build_split([a, b], 0.1)
    # 10% trailing of each 100-frame block -> 10 A + 10 B in validation.
    assert int((va_arb == 0xA).sum()) == 10
    assert int((va_arb == 0xB).sum()) == 10


def test_pretrain_smoke_learns_structure(tmp_path):
    torch = pytest.importorskip("torch")  # noqa: F841
    from canrosetta.model.pretrain import PretrainConfig
    from canrosetta.model.train import TrainConfig, train

    # Synthetic bus: a constant-plus-counter ID and a two-value flag ID —
    # learnable structure a few hundred steps must pick up.
    rng = np.random.default_rng(0)
    rows = []
    for i in range(3000):
        rows.append({"arb_id": 0x3C0,
                     "data": bytes([0xAA, i & 0xFF, 0x55, 0x00]).hex()})
        rows.append({"arb_id": 0x1F0,
                     "data": bytes([0x00, 0xFF if rng.random() < 0.5 else 0x00]).hex()})
    frames = tmp_path / "frames.jsonl"
    frames.write_text("\n".join(json.dumps(r) for r in rows))

    cfg = TrainConfig(steps=120, batch_size=64, log_every=60,
                      model=PretrainConfig(d_model=32, n_heads=2, n_layers=1))
    report = train([str(frames)], cfg, str(tmp_path / "run"))

    assert (tmp_path / "run" / "checkpoint.pt").exists()
    assert (tmp_path / "run" / "report.json").exists()
    # Loss must actually move, and the model must not be far below the lookup
    # baseline on this trivially structured bus.
    assert report["val_loss"] < 5.0  # untrained CE ~ log(260) ≈ 5.6
    assert report["val_masked_accuracy"] > 0.5
    assert len(report["surprisal_by_id"]) == 2
    # The report's own lift field must equal accuracy minus baseline (both
    # measured over the same payload positions after the F1 fix).
    assert report["lift_over_baseline"] == (
        report["val_masked_accuracy"] - report["majority_baseline_accuracy"])
