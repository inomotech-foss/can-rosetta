"""Turn raw CAN frames into token sequences for self-supervised pretraining.

A frame becomes: ``[BOF] <id-hi> <id-lo> <b0> <b1> ... <b_dlc-1> [EOF]``, where
byte values 0-255 map to token ids 0-255 and a handful of special tokens follow.
This byte-level vocabulary keeps the model architecture-agnostic and lets it
learn structure (counters, checksums, multiplexing, periodicity) directly from
bytes, the way a language model learns from characters.

Pure numpy so it's usable and testable without PyTorch.
"""

from __future__ import annotations

import numpy as np

from ..session import CanFrames

PAD, BOF, EOF, MASK = 256, 257, 258, 259
VOCAB_SIZE = 260

# A frame is ``[BOF, id0, id1, id2, id3, b0..b_{dlc-1}, EOF, PAD...]``. The
# arbitration id is encoded big-endian in FOUR bytes so an extended (29-bit) id
# is represented in full — a 2-byte id would alias distinct 29-bit ids (e.g. the
# UDS diagnostic pair 0x18DAF110 / 0x18DBF110) onto the same tokens. The id
# bytes are *context*: they are never masked and never scored (a model that had
# to "predict" the id it was told would inflate accuracy), so PREFIX_LEN marks
# where the maskable payload begins.
ID_BYTES = 4
PREFIX_LEN = 1 + ID_BYTES  # BOF + id bytes


class FrameTokenizer:
    """Byte-level tokenizer for CAN frames."""

    def encode_frame(self, arb_id: int, data: bytes) -> list[int]:
        id_bytes = [(arb_id >> (8 * (ID_BYTES - 1 - i))) & 0xFF for i in range(ID_BYTES)]
        return [BOF, *id_bytes, *list(data), EOF]

    def encode_stream(self, frames: CanFrames, *, max_frames: int | None = None) -> np.ndarray:
        """Encode a whole log to a padded 2D array ``[n_frames, seq_len]``."""
        n = len(frames) if max_frames is None else min(len(frames), max_frames)
        seqs = [self.encode_frame(int(frames.arb_id[i]), frames.data[i]) for i in range(n)]
        if not seqs:
            return np.empty((0, 0), dtype=np.int32)
        seq_len = max(len(s) for s in seqs)
        out = np.full((len(seqs), seq_len), PAD, dtype=np.int32)
        for i, s in enumerate(seqs):
            out[i, : len(s)] = s
        return out

    def mask_tokens(
        self, tokens: np.ndarray, rng: np.random.Generator, p: float = 0.15,
        prefix_len: int = PREFIX_LEN,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Build a masked-language-model target.

        Returns ``(masked_input, labels)`` where labels are -100 except at masked
        positions. Only **payload** bytes are maskable: BOF/EOF/PAD are specials
        (>= 256), and the leading ``prefix_len`` tokens (BOF + the id bytes) are
        context the model is *given*, not asked to predict — masking them would
        inflate accuracy with near-trivial guesses. This is the pretraining
        objective consumed by :mod:`canrosetta.model.pretrain`.
        """
        masked = tokens.copy()
        labels = np.full(tokens.shape, -100, dtype=np.int64)
        maskable = tokens < 256  # only real bytes, never PAD/BOF/EOF/MASK
        maskable[:, :prefix_len] = False  # id bytes are context, not targets
        draw = rng.random(tokens.shape) < p
        sel = maskable & draw
        labels[sel] = tokens[sel]
        masked[sel] = MASK
        return masked, labels
