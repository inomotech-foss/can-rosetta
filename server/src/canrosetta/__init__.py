"""CAN-Rosetta server: align, extract, identify, and model vehicle CAN signals.

The public entry points most callers want:

    from canrosetta import load_session, identify_session

`load_session` reads a session directory (see docs/data-format.md) into memory;
`identify_session` runs the full Stage 2-4 pipeline and returns ranked signal
hypotheses. The learned foundation model lives under `canrosetta.model`.
"""

from .identify import identify_session
from .session import Session, load_session

__all__ = ["Session", "load_session", "identify_session"]
__version__ = "0.1.0"
