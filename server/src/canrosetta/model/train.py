"""Pretraining loop for the CAN byte model — the missing half of pretrain.py.

``pretrain.py`` defines the masked-byte Transformer and a single optimization
step; this module turns it into a runnable pipeline::

    canrosetta pretrain <session-dir-or-frames.jsonl> ... --out runs/evito

Honest scope: pretrained on ONE vehicle (or one mode, e.g. a charge session),
the encoder learns *that bus's* byte structure — per-ID field layouts, counters,
constants, mux patterns. That is already useful on the same vehicle (per-byte
surprisal maps segment fields; embeddings cluster related IDs) and it validates
the training recipe, but cross-vehicle transfer needs a multi-vehicle corpus.
Every session logged by the fleet extends the corpus; re-run this on all of it.

Design choices, deliberately boring:

- Only **organic (rx) payload bytes** are modelled. The tool's own discovery
  probes (``direction == "tx"``) are excluded — training on our injected UDS
  requests would present them as discovered bus structure — mirroring
  ``CanFrames.by_id(rx_only=True)``. Arbitration-id bytes are *context* (given,
  never masked/scored); only payload bytes are targets. Baseline, model metric
  and surprisal report all score the **same** payload positions, keyed on the
  **full** arbitration id (see tokenizer.py: 4-byte id, so 29-bit ids don't
  alias).
- The corpus lives in RAM as an int16 token matrix ``[n_frames, seq_len]``
  (5M classic-CAN frames ≈ 130 MB) built by a vectorized packer rather than the
  per-frame ``FrameTokenizer.encode_frame`` (equivalence is unit-tested).
- The train/val split is **per-session and time-ordered** (val = the trailing
  fraction of *each* input file): CAN frames repeat heavily, so a random split
  — or a global split over files concatenated in arbitrary CLI order — would
  leak near-duplicates from train into val and flatter the model.
- The yardstick is a **majority baseline**: predict each masked byte as the most
  frequent value seen at that (full-id, position) in training. A transformer
  that cannot beat a lookup table has learned nothing beyond marginals — report
  both numbers side by side, always, over identical positions.
- The per-ID **surprisal report** (computed on held-out val frames, per the
  anti-leakage rationale above) is the interpretable output: mean masked-byte
  cross-entropy (bits) per byte position. Constants/counters score near zero;
  physical-signal bytes score high — a field-segmentation signal that exists
  the moment pretraining finishes, long before any labels.

PyTorch is imported lazily inside :func:`train`, same as pretrain.py; corpus
loading and the baseline are pure numpy.
"""

from __future__ import annotations

import dataclasses
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from ..session import _check_schema_version
from .pretrain import PretrainConfig, build_model, pretrain_step
from .tokenizer import BOF, EOF, ID_BYTES, PAD, PREFIX_LEN, FrameTokenizer


@dataclass
class TrainConfig:
    steps: int = 10_000
    batch_size: int = 512
    val_frac: float = 0.05
    seed: int = 0
    log_every: int = 200
    max_frames: int | None = None
    report_top_ids: int = 24
    model: PretrainConfig | None = None  # None -> PretrainConfig() defaults


# A parsed session file: per-file so the train/val split can be per-file.
@dataclass
class FrameBlock:
    arb_ids: np.ndarray       # uint32 [n]
    payloads: list[bytes]     # len n
    source: str


# --------------------------------------------------------------------------
# Corpus
# --------------------------------------------------------------------------

def _frames_file(path: Path) -> Path:
    """Resolve one input path to a frames file. Accepts a session directory
    (``can/frames.{jsonl,parquet}``), a bare directory holding one of those, or
    a ``.jsonl``/``.parquet`` file directly. Parquet is a first-class input,
    exactly as for ``identify``/``fingerprint``."""
    if path.is_file():
        return path
    if path.is_dir():
        for sub in (path / "can", path):
            for name in ("frames.jsonl", "frames.parquet"):
                if (sub / name).exists():
                    return sub / name
        raise FileNotFoundError(f"no can/frames.jsonl or frames.parquet under {path}")
    raise FileNotFoundError(str(path))


def _validate_schema(frames_file: Path) -> None:
    """Re-establish the 'session files are untrusted — validate, don't trust'
    policy that ``load_session`` enforces: reject a session whose manifest
    declares an unsupported schema major, rather than crashing deep in parsing.
    A bare frames file with no manifest alongside is trained on as-is."""
    for base in (frames_file.parent, frames_file.parent.parent):
        manifest = base / "manifest.json"
        if manifest.exists():
            _check_schema_version(json.loads(manifest.read_text()), "manifest")
            return


def _iter_rx_rows(frames_file: Path):
    """Yield ``(arb_id, payload_bytes)`` for organic (rx) frames only, from a
    jsonl or parquet frames file. tx probe traffic is skipped (see module doc)."""
    if frames_file.suffix == ".parquet":
        try:
            import pyarrow.parquet as pq
        except ImportError as exc:  # noqa: TRY003
            raise ImportError(
                "reading frames.parquet needs pyarrow; install canrosetta[parquet] "
                "or export the session to frames.jsonl"
            ) from exc
        cols = pq.read_table(frames_file).to_pydict()
        directions = cols.get("direction") or ["rx"] * len(cols["arb_id"])
        for aid, data, direction in zip(cols["arb_id"], cols["data"], directions, strict=True):
            if direction != "rx":
                continue
            payload = bytes.fromhex(data) if isinstance(data, str) else bytes(data)
            yield int(aid), payload
    else:
        with frames_file.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                if r.get("direction", "rx") != "rx":
                    continue
                raw = r["data"]
                yield int(r["arb_id"]), (bytes.fromhex(raw) if isinstance(raw, str) else bytes(raw))


def load_blocks(paths: list[str], *, max_frames: int | None = None) -> list[FrameBlock]:
    """Parse each input path into a :class:`FrameBlock` of organic frames, in
    acquisition order within each file. ``max_frames`` caps the total across
    files. Raises if nothing was read."""
    blocks: list[FrameBlock] = []
    total = 0
    for p in paths:
        frames_file = _frames_file(Path(p))
        _validate_schema(frames_file)
        arb_ids: list[int] = []
        payloads: list[bytes] = []
        for aid, payload in _iter_rx_rows(frames_file):
            if max_frames is not None and total >= max_frames:
                break
            arb_ids.append(aid)
            payloads.append(payload)
            total += 1
        if arb_ids:
            ids = np.asarray(arb_ids, dtype=np.uint32)
            blocks.append(FrameBlock(ids, payloads, str(frames_file)))
        if max_frames is not None and total >= max_frames:
            break
    if not blocks:
        raise ValueError(f"no organic (rx) frames found in {paths}")
    return blocks


def pack_tokens(arb_ids: np.ndarray, payloads: list[bytes], *,
                width: int | None = None) -> np.ndarray:
    """Vectorized ``FrameTokenizer.encode_frame`` over a corpus:
    ``[BOF, id0, id1, id2, id3, b0..b_{dlc-1}, EOF]`` right-padded with PAD.
    ``width`` pins the column count so blocks from different files share one
    matrix shape; default fits this batch. int16 covers the 260-token vocab."""
    n = len(payloads)
    if n == 0:
        return np.empty((0, width or PREFIX_LEN + 1), dtype=np.int16)
    dlc = np.fromiter((len(p) for p in payloads), dtype=np.int64, count=n)
    need = PREFIX_LEN + int(dlc.max()) + 1
    width = need if width is None else max(width, need)
    tokens = np.full((n, width), PAD, dtype=np.int16)
    tokens[:, 0] = BOF
    for i in range(ID_BYTES):  # big-endian id bytes, cols 1..ID_BYTES
        tokens[:, 1 + i] = (arb_ids >> (8 * (ID_BYTES - 1 - i))) & 0xFF
    flat = np.frombuffer(b"".join(payloads), dtype=np.uint8)
    rows = np.repeat(np.arange(n), dlc)
    cols = PREFIX_LEN + (np.arange(len(flat)) - np.repeat(np.cumsum(dlc) - dlc, dlc))
    tokens[rows, cols] = flat
    tokens[np.arange(n), PREFIX_LEN + dlc] = EOF
    return tokens


def build_split(blocks: list[FrameBlock], val_frac: float
                ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Pack all blocks to one common width and split each block's *trailing*
    ``val_frac`` into validation (per-file time-ordered hold-out). Returns
    ``(train_tokens, train_arb, val_tokens, val_arb)``."""
    if not 0.0 < val_frac < 1.0:
        raise ValueError(f"val_frac must be in (0, 1), got {val_frac}")
    width = max(PREFIX_LEN + max(len(p) for b in blocks for p in b.payloads) + 1, PREFIX_LEN + 1)
    tr_tok, tr_arb, va_tok, va_arb = [], [], [], []
    for b in blocks:
        tokens = pack_tokens(b.arb_ids, b.payloads, width=width)
        n = len(tokens)
        n_val = int(n * val_frac)
        # Keep at least one frame on each side when the block allows it, so a
        # small session still contributes both train and val rows.
        if n >= 2:
            n_val = min(max(1, n_val), n - 1)
        cut = n - n_val
        tr_tok.append(tokens[:cut])
        tr_arb.append(b.arb_ids[:cut])
        va_tok.append(tokens[cut:])
        va_arb.append(b.arb_ids[cut:])
    train_tokens = np.concatenate(tr_tok) if tr_tok else np.empty((0, width), np.int16)
    val_tokens = np.concatenate(va_tok) if va_tok else np.empty((0, width), np.int16)
    train_arb = np.concatenate(tr_arb) if tr_arb else np.empty((0,), np.uint32)
    val_arb = np.concatenate(va_arb) if va_arb else np.empty((0,), np.uint32)
    if len(train_tokens) == 0 or len(val_tokens) == 0:
        raise ValueError(
            f"corpus too small to split: {len(train_tokens)} train / {len(val_tokens)} val "
            f"frames at val_frac={val_frac}. Provide more frames or adjust --val-frac."
        )
    return train_tokens, train_arb, val_tokens, val_arb


# --------------------------------------------------------------------------
# Majority baseline (the number to beat) — payload positions, full id
# --------------------------------------------------------------------------

def _payload_keys(tok: np.ndarray, arb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Flatten payload cells to ``(key, byte_value)`` where key = full arb id ×
    position — the exact cells :func:`FrameTokenizer.mask_tokens` can mask, so
    the baseline and the model metric score identical positions."""
    n, width = tok.shape
    payload_cols = np.arange(PREFIX_LEN, width - 1)
    pos = np.broadcast_to(payload_cols, (n, len(payload_cols)))
    ids = np.broadcast_to(arb.astype(np.int64)[:, None], pos.shape)
    val = tok[:, PREFIX_LEN:width - 1]
    keep = val < 256  # real payload bytes only; PAD/EOF in short frames excluded
    key = (ids * width + pos)[keep]
    return key, val[keep].astype(np.int64)


def majority_baseline(train_tokens: np.ndarray, train_arb: np.ndarray,
                      val_tokens: np.ndarray, val_arb: np.ndarray) -> float:
    """Accuracy of predicting each val payload byte as the most frequent byte
    seen at the same (full arb id, position) in training. Pure lookup."""
    train_key, train_val = _payload_keys(train_tokens, train_arb)
    if len(train_key) == 0:
        return 0.0
    uniq, inv = np.unique(train_key, return_inverse=True)
    counts = np.zeros((len(uniq), 256), dtype=np.int64)
    np.add.at(counts, (inv, train_val), 1)
    majority = {int(k): int(b) for k, b in zip(uniq, counts.argmax(axis=1), strict=True)}

    val_key, val_val = _payload_keys(val_tokens, val_arb)
    if len(val_key) == 0:
        return 0.0
    pred = np.fromiter((majority.get(int(k), -1) for k in val_key),
                       dtype=np.int64, count=len(val_key))
    return float((pred == val_val).mean())


# --------------------------------------------------------------------------
# Training
# --------------------------------------------------------------------------

def train(paths: list[str], cfg: TrainConfig, out_dir: str) -> dict:
    """Run masked-byte pretraining; write ``checkpoint.pt`` + ``report.json``
    under ``out_dir`` and return the report dict."""
    try:
        import torch
    except ImportError as exc:  # noqa: TRY003
        raise ImportError(
            "pretraining needs PyTorch; install with `pip install canrosetta[model]`"
        ) from exc

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    # Independent, deterministic streams so validation sampling never perturbs
    # the training-batch sequence (a fixed --seed must fix the trained model).
    train_rng = np.random.default_rng(cfg.seed)
    val_rng = np.random.default_rng(cfg.seed + 1)
    torch.manual_seed(cfg.seed)

    t0 = time.time()
    blocks = load_blocks(paths, max_frames=cfg.max_frames)
    train_tokens, train_arb, val_tokens, val_arb = build_split(blocks, cfg.val_frac)
    width = train_tokens.shape[1]
    n_frames = len(train_tokens) + len(val_tokens)
    print(f"[pretrain] corpus: {n_frames} rx frames x {width} tokens "
          f"({len(train_tokens)} train / {len(val_tokens)} val) from {len(blocks)} file(s), "
          f"loaded in {time.time() - t0:.0f}s")

    baseline_acc = majority_baseline(train_tokens, train_arb, val_tokens, val_arb)
    print(f"[pretrain] majority baseline (per-id/position lookup): {baseline_acc:.4f}")

    # Copy the model config (don't mutate the caller's) and fit the sequence len.
    base_cfg = cfg.model or PretrainConfig()
    model_cfg = dataclasses.replace(base_cfg, max_seq_len=max(base_cfg.max_seq_len, width))
    model = build_model(model_cfg)
    optimizer = torch.optim.AdamW(model.parameters(), lr=model_cfg.lr)
    tokenizer = FrameTokenizer()

    def batch(source: np.ndarray, size: int, rng: np.random.Generator):
        idx = rng.integers(0, len(source), size=size)
        masked, labels = tokenizer.mask_tokens(source[idx], rng, p=model_cfg.mask_prob)
        return torch.from_numpy(masked.astype(np.int64)), torch.from_numpy(labels)

    @torch.no_grad()
    def val_metrics(n_batches: int = 8) -> tuple[float, float]:
        was_training = model.training
        model.eval()
        try:
            losses, correct, total = [], 0, 0
            for _ in range(n_batches):
                x, y = batch(val_tokens, cfg.batch_size, val_rng)
                logits = model(x)
                loss = torch.nn.functional.cross_entropy(
                    logits.view(-1, logits.size(-1)), y.view(-1), ignore_index=-100)
                losses.append(float(loss))
                masked = y != -100
                correct += int((logits.argmax(-1)[masked] == y[masked]).sum())
                total += int(masked.sum())
        finally:
            model.train(was_training)
        return float(np.mean(losses)), correct / max(1, total)

    losses: list[float] = []
    t0 = time.time()
    for step in range(1, cfg.steps + 1):
        x, y = batch(train_tokens, cfg.batch_size, train_rng)
        losses.append(pretrain_step(model, x, y, optimizer))
        if step % cfg.log_every == 0 or step == cfg.steps:
            vloss, vacc = val_metrics()
            rate = step * cfg.batch_size / (time.time() - t0)
            recent = float(np.mean(losses[-cfg.log_every:]))
            print(f"[pretrain] step {step}/{cfg.steps}  train_loss={recent:.4f}  "
                  f"val_loss={vloss:.4f}  val_masked_acc={vacc:.4f}  "
                  f"(baseline {baseline_acc:.4f})  {rate:.0f} frames/s", flush=True)

    vloss, vacc = val_metrics(n_batches=32)
    report = {
        "frames": int(n_frames),
        "train_frames": int(len(train_tokens)),
        "val_frames": int(len(val_tokens)),
        "steps": cfg.steps,
        "batch_size": cfg.batch_size,
        "final_train_loss": float(np.mean(losses[-cfg.log_every:])),
        "val_loss": vloss,
        "val_masked_accuracy": vacc,
        "majority_baseline_accuracy": baseline_acc,
        "lift_over_baseline": vacc - baseline_acc,
        "model": asdict(model_cfg),
        "surprisal_by_id": surprisal_report(model, val_tokens, val_arb,
                                            top_ids=cfg.report_top_ids),
    }
    torch.save({"model_state": model.state_dict(), "config": asdict(model_cfg),
                "train_config": {**asdict(cfg), "model": asdict(model_cfg)},
                "report": {k: v for k, v in report.items() if k != "surprisal_by_id"}},
               out / "checkpoint.pt")
    (out / "report.json").write_text(json.dumps(report, indent=2))
    print(f"[pretrain] saved {out / 'checkpoint.pt'} and report.json  "
          f"(val acc {vacc:.4f} vs baseline {baseline_acc:.4f})")
    return report


def surprisal_report(model, tokens: np.ndarray, arb_ids: np.ndarray,
                     *, top_ids: int = 24, frames_per_id: int = 256) -> list[dict]:
    """Per-arb-id, per-byte-position mean masked surprisal (bits) on the given
    (held-out) frames.

    For each of the most frequent ids, mask one payload position at a time
    across sampled frames and record the model's -log2 p(true byte). Near-zero
    bits = the model finds the byte predictable (constant, counter,
    checksum-of-seen); high bits = an informative field. This map is the
    pretrained model's first concrete contribution to field segmentation — no
    labels involved. Evaluated on held-out frames (see module doc) so memorised
    frames don't bias surprisal low.
    """
    import torch

    from .tokenizer import MASK

    if len(tokens) == 0:
        return []
    ids, counts = np.unique(arb_ids, return_counts=True)
    order = np.argsort(counts)[::-1][:top_ids]
    rng = np.random.default_rng(0)
    out: list[dict] = []
    was_training = model.training
    model.eval()
    try:
        with torch.no_grad():
            for aid, cnt in zip(ids[order], counts[order], strict=True):
                rows = np.flatnonzero(arb_ids == aid)
                rows = rng.choice(rows, size=min(frames_per_id, len(rows)), replace=False)
                sample = tokens[rows].astype(np.int64)
                width = sample.shape[1]
                bits_per_pos: list[float | None] = []
                for pos in range(PREFIX_LEN, width - 1):  # payload bytes only
                    true = sample[:, pos].copy()
                    valid = true < 256  # skip PAD/EOF at this position (short frames)
                    if not valid.any():
                        bits_per_pos.append(None)
                        continue
                    masked = sample[valid].copy()
                    masked[:, pos] = MASK
                    logits = model(torch.from_numpy(masked))[:, pos, :]
                    logp = torch.log_softmax(logits, dim=-1)
                    nll = -logp[np.arange(valid.sum()), true[valid]] / np.log(2.0)
                    bits_per_pos.append(float(nll.mean()))
                out.append({"arb_id": int(aid), "val_frames": int(cnt),
                            "surprisal_bits_per_byte": bits_per_pos})
    finally:
        model.train(was_training)
    return out
