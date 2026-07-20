"""Minimal, read-only UDS client over a :class:`Transport`.

Implements ReadDataByIdentifier (0x22) against the standard OBD ECU addresses,
with a tiny catalog of well-known DIDs. Per SAFETY.md, only the read-style
services 0x22 (ReadDataByIdentifier) and 0x19 (ReadDTCInformation) may ever be
issued -- :func:`assert_read_only_service` enforces that and is used as the test
harness's guard.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .transport import (
    OBD_PHYSICAL_TX_BASE,
    OBD_RESP_BASE,
    Transport,
    phys_req_29,
    phys_resp_29,
)

# Read-style UDS services permitted by SAFETY.md. Everything else is refused:
# no 0x2E write, 0x31 routine, 0x2F IO-control, 0x10 session, 0x27 security,
# 0x11 reset, etc.
SAFE_UDS_SERVICES = frozenset({0x22, 0x19})

# Standardized DIDs worth probing (ISO 14229-1 Annex C identification block).
# These are manufacturer-independent and safe to read in the default session.
DID_CATALOG: Dict[int, str] = {
    0xF180: "boot_software_id",
    0xF181: "application_software_id",
    0xF182: "application_data_id",
    0xF183: "boot_software_fingerprint",
    0xF184: "application_software_fingerprint",
    0xF186: "active_diagnostic_session",
    0xF187: "vehicle_manufacturer_spare_part_number",
    0xF188: "vehicle_manufacturer_ecu_sw_number",
    0xF189: "vehicle_manufacturer_ecu_sw_version",
    0xF18A: "system_supplier_id",
    0xF18B: "ecu_manufacturing_date",
    0xF18C: "ecu_serial_number",
    0xF190: "vin",
    0xF191: "vehicle_manufacturer_ecu_hw_number",
    0xF192: "system_supplier_ecu_hw_number",
    0xF193: "system_supplier_ecu_hw_version",
    0xF194: "system_supplier_ecu_sw_number",
    0xF195: "system_supplier_ecu_sw_version",
    0xF197: "system_name",
    0xF19E: "asam_odx_file_id",
    0xF1A0: "vehicle_manufacturer_specific_a0",
    0xF1A1: "vehicle_manufacturer_specific_a1",
}

# Standard 11-bit ECU address pairs: physical request 0x7E0..0x7E7 -> response +8.
STANDARD_ECUS = [
    (OBD_PHYSICAL_TX_BASE + i, OBD_RESP_BASE + i) for i in range(8)
]

# Extended 29-bit ECU address pairs, targeting the commonly-populated logical
# ECU addresses first (gateway, powertrain, chassis, body, EV subsystems). The
# recon "deep" mode sweeps the whole 0x00..0xFF ECU space instead.
COMMON_ECU_ADDRS_29 = [
    0x00, 0x01, 0x02, 0x03, 0x07, 0x10, 0x11, 0x12, 0x14, 0x17, 0x18, 0x1A,
    0x25, 0x28, 0x29, 0x40, 0x42, 0x44, 0x50, 0x60, 0x62, 0x72, 0x80, 0x87,
]
EXTENDED_ECUS = [(phys_req_29(e), phys_resp_29(e)) for e in COMMON_ECU_ADDRS_29]


def ecu_pairs_29(ecus) -> List:
    """Build (tx_id, rx_id) 29-bit pairs for the given logical ECU addresses."""
    return [(phys_req_29(e), phys_resp_29(e)) for e in ecus]


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

    def read_dtcs(self, status_mask: int = 0xFF):
        """UDS 0x19 sub 0x02 (reportDTCByStatusMask). Read-only, default session.

        Returns a list of ``(dtc, status)`` tuples (``dtc`` a 3-byte int), or
        ``None`` on no/negative response.
        """
        resp = self.send_request(0x19, bytes([0x02, status_mask & 0xFF]))
        if resp is None or len(resp) < 3 or resp[1] != 0x02:
            return None
        body = resp[3:]  # after 59 02 <statusAvailabilityMask>
        out = []
        for i in range(0, len(body) - 3, 4):
            dtc = (body[i] << 16) | (body[i + 1] << 8) | body[i + 2]
            out.append((dtc, body[i + 3]))
        return out


def decode_dtc(dtc: int) -> str:
    """Render a 3-byte UDS DTC as the SAE J2012 code (e.g. 0x012345 -> 'P0123')."""
    b0 = (dtc >> 16) & 0xFF
    b1 = (dtc >> 8) & 0xFF
    letter = "PCBU"[(b0 >> 6) & 0x3]
    return f"{letter}{(b0 >> 4) & 0x3}{b0 & 0xF:X}{b1:02X}"
