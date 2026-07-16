"""Minimal, read-only UDS client over a :class:`Transport`.

Implements ReadDataByIdentifier (0x22) against the standard OBD ECU addresses,
with a tiny catalog of well-known DIDs. Per SAFETY.md, only the read-style
services 0x22 (ReadDataByIdentifier) and 0x19 (ReadDTCInformation) may ever be
issued -- :func:`assert_read_only_service` enforces that and is used as the test
harness's guard.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .transport import OBD_RESP_BASE, OBD_PHYSICAL_TX_BASE, Transport

# Read-style UDS services permitted by SAFETY.md. Everything else is refused:
# no 0x2E write, 0x31 routine, 0x2F IO-control, 0x10 session, 0x27 security,
# 0x11 reset, etc.
SAFE_UDS_SERVICES = frozenset({0x22, 0x19})

# Standardized DIDs worth probing.
DID_CATALOG: Dict[int, str] = {
    0xF190: "vin",
    0xF18C: "ecu_serial_number",
    0xF195: "system_supplier_ecu_sw_version",
}

# Standard ECU address pairs: physical request 0x7E0..0x7E7 -> response +8.
STANDARD_ECUS = [
    (OBD_PHYSICAL_TX_BASE + i, OBD_RESP_BASE + i) for i in range(8)
]


def did_hex(did: int) -> str:
    return f"0x{did:04X}"


def assert_read_only_service(sid: int) -> None:
    """Refuse any UDS service that is not a safe, read-style service.

    This is the single choke-point every UDS request passes through. It exists
    so that a coding mistake (or a well-meaning PR) cannot emit a state-changing
    service such as WriteDataByIdentifier or SecurityAccess.
    """
    if sid not in SAFE_UDS_SERVICES:
        raise ValueError(
            f"UDS service 0x{sid:02X} is not a permitted read-only service "
            f"(allowed: {sorted(hex(s) for s in SAFE_UDS_SERVICES)})"
        )


class UdsClient:
    """Read-only UDS client bound to one ECU address pair."""

    def __init__(self, transport: Transport,
                 tx_id: int = OBD_PHYSICAL_TX_BASE, rx_id: int = OBD_RESP_BASE,
                 timeout: float = 1.0):
        self.transport = transport
        self.tx_id = tx_id
        self.rx_id = rx_id
        self.timeout = timeout

    def send_request(self, sid: int, payload: bytes = b"") -> Optional[bytes]:
        """Send a UDS request after passing the read-only safety guard.

        Returns the response PDU, or ``None`` on timeout / negative response.
        """
        assert_read_only_service(sid)  # hard safety guard
        pdu = bytes([sid]) + bytes(payload)
        resp = self.transport.request(self.tx_id, self.rx_id, pdu, self.timeout)
        if not resp:
            return None
        if resp[0] == 0x7F:  # negative response
            return None
        if resp[0] != sid + 0x40:
            return None
        return resp

    def read_data_by_identifier(self, did: int) -> Optional[bytes]:
        """UDS 0x22. Returns the DID's data bytes, or ``None``."""
        resp = self.send_request(0x22, bytes([(did >> 8) & 0xFF, did & 0xFF]))
        if resp is None or len(resp) < 3:
            return None
        # 0x62 <did_hi> <did_lo> <data...>
        if (resp[1] << 8 | resp[2]) != did:
            return None
        return resp[3:]

    def probe_dids(self, dids) -> List[int]:
        """Return which of ``dids`` this ECU answers."""
        responding: List[int] = []
        for did in dids:
            if self.read_data_by_identifier(did) is not None:
                responding.append(did)
        return responding
