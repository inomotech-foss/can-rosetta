"""canrosetta_edge -- the in-vehicle (edge) component of CAN-Rosetta.

Discovery (Stage 1a) and continuous logging (Stage 1b) for a vehicle CAN bus,
producing the shared session format consumed by the CAN-Rosetta server.

Read-only by design: see SAFETY.md. Only OBD services 0x01/0x09 and UDS
services 0x22/0x19 are ever issued.
"""

from __future__ import annotations

from .config import EdgeConfig
from .transport import (
    ElmTransport,
    Frame,
    SimulatedTransport,
    SocketCanTransport,
    Transport,
)

__version__ = "0.1.0"
SCHEMA_VERSION = "1.0.0"

__all__ = [
    "__version__",
    "SCHEMA_VERSION",
    "EdgeConfig",
    "Transport",
    "Frame",
    "SimulatedTransport",
    "SocketCanTransport",
    "ElmTransport",
]
