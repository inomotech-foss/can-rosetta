"""The CAN-Rosetta foundation model (Stage 5).

Two halves:

- :mod:`canrosetta.model.fingerprint` and :mod:`canrosetta.model.tokenizer` are
  pure-numpy and always available. Fingerprints turn a frame/candidate into a
  fixed behavioral feature vector; the tokenizer turns raw frames into token
  sequences for pretraining. Both are used by the classical baseline *and* the
  learned model, and both are unit-tested.

- :mod:`canrosetta.model.pretrain` is the self-supervised Transformer. It needs
  PyTorch (``pip install canrosetta[model]``) and is imported lazily so the rest
  of the package works without it.

The design rationale is in docs/methodology.md (Stage 5).
"""

from .fingerprint import Fingerprint, fingerprint_frame
from .tokenizer import FrameTokenizer

__all__ = ["Fingerprint", "fingerprint_frame", "FrameTokenizer"]
