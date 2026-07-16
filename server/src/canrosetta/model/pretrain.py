"""Self-supervised pretraining of the CAN byte model (research scaffold).

This is the learned half of Stage 5. A small Transformer encoder is pretrained
with a masked-byte objective on large volumes of *unlabelled* multi-vehicle CAN
(cheap to collect). The resulting encoder produces per-frame embeddings that a
lightweight head then maps to signal types using the aligned phone/OBD/OCR labels
from the classical baseline (expensive, scarce). Because the encoder already
understands bus structure, far fewer labelled drives are needed to decode a new
vehicle.

PyTorch is required and imported lazily (``pip install canrosetta[model]``). The
tokenizer and fingerprints in the sibling modules are the numpy pieces that work
without it; this module is intentionally the only torch-dependent one.
"""

from __future__ import annotations

from dataclasses import dataclass

from .tokenizer import VOCAB_SIZE


@dataclass
class PretrainConfig:
    d_model: int = 128
    n_heads: int = 4
    n_layers: int = 4
    max_seq_len: int = 16
    dropout: float = 0.1
    lr: float = 3e-4
    mask_prob: float = 0.15


def build_model(cfg: PretrainConfig | None = None):
    """Construct the masked-byte Transformer. Requires torch.

    Returns an ``nn.Module`` whose ``forward(tokens)`` yields per-position logits
    over the byte vocabulary. Kept small and standard on purpose — the point of
    the project is the data/alignment pipeline, not a novel architecture.
    """
    cfg = cfg or PretrainConfig()
    try:
        import torch
        from torch import nn
    except ImportError as exc:  # noqa: TRY003
        raise ImportError(
            "the learned model needs PyTorch; install with `pip install canrosetta[model]`"
        ) from exc

    class CanByteModel(nn.Module):
        def __init__(self, c: PretrainConfig):
            super().__init__()
            self.tok = nn.Embedding(VOCAB_SIZE, c.d_model)
            self.pos = nn.Embedding(c.max_seq_len, c.d_model)
            layer = nn.TransformerEncoderLayer(
                c.d_model, c.n_heads, dim_feedforward=4 * c.d_model,
                dropout=c.dropout, batch_first=True,
            )
            self.encoder = nn.TransformerEncoder(layer, c.n_layers)
            self.head = nn.Linear(c.d_model, VOCAB_SIZE)
            self.max_seq_len = c.max_seq_len

        def forward(self, tokens):  # tokens: [B, L] long
            b, length = tokens.shape
            pos = torch.arange(length, device=tokens.device).unsqueeze(0).expand(b, length)
            h = self.tok(tokens) + self.pos(pos)
            h = self.encoder(h)
            return self.head(h)

        def frame_embedding(self, tokens):
            """Mean-pooled encoder output — the transferable per-frame feature."""
            b, length = tokens.shape
            pos = torch.arange(length, device=tokens.device).unsqueeze(0).expand(b, length)
            h = self.encoder(self.tok(tokens) + self.pos(pos))
            return h.mean(dim=1)

    return CanByteModel(cfg)


def pretrain_step(model, batch_tokens, labels, optimizer):
    """One masked-byte optimization step. Requires torch. Returns the loss float."""
    from torch.nn import functional as F  # noqa: N812

    logits = model(batch_tokens)
    loss = F.cross_entropy(logits.view(-1, logits.size(-1)), labels.view(-1), ignore_index=-100)
    optimizer.zero_grad()
    loss.backward()
    optimizer.step()
    return float(loss.detach())
