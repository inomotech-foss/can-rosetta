"""Opt-in *intrusive* UDS probing -- OUTSIDE the read-only safety contract.

Everything in this module changes ECU state and is therefore disabled by
default. It runs only when the caller passes ``allow=True`` (surfaced as the
``--allow-session`` CLI flag), and it is kept in its own module, behind its own
guard, so the read-only core (:mod:`obd`, :mod:`uds`, :mod:`discovery`) can never
accidentally reach it.

What it does, and the rails it keeps:

* Opens an **extended diagnostic session** (``0x10 0x03``) -- never the
  programming session (``0x02``).
* Holds the session with **TesterPresent** (``0x3E 0x80``, suppressed response).
* Re-reads DIDs (``0x22``) that the default session refused, to see what the
  session unlocks.
* Restores the **default session** (``0x10 0x01``) on exit.

It deliberately does **not** touch SecurityAccess (``0x27``) -- a wrong key can
trip an ECU's attempt counter and lock it out -- nor any write/routine/reset.

**Never run this on a moving vehicle:** an extended session can suppress an
ECU's normal periodic messaging.
"""

from __future__ import annotations

import time
from typing import List, Optional

# Services this intrusive path is permitted to emit (still no 0x27/0x2E/0x31/...).
ACTIVE_SERVICES = frozenset({0x10, 0x3E, 0x22, 0x19})

EXTENDED_SESSION = 0x03
DEFAULT_SESSION = 0x01

NRC_NAMES = {
    0x11: "serviceNotSupported", 0x12: "subFunctionNotSupported",
    0x22: "conditionsNotCorrect", 0x31: "requestOutOfRange",
    0x33: "securityAccessDenied", 0x35: "invalidKey",
    0x7E: "subFunctionNotSupportedInActiveSession",
    0x7F: "serviceNotSupportedInActiveSession",
}


def assert_active_allowed(allow: bool) -> None:
    if not allow:
        raise PermissionError(
            "intrusive UDS (session control) is disabled; pass --allow-session "
            "and only with a stationary vehicle (see SAFETY.md)"
        )


def nrc_name(nrc: int) -> str:
    return NRC_NAMES.get(nrc, f"0x{nrc:02X}"
                         + ("(mfr-specific)" if 0xF0 <= nrc <= 0xFE else ""))


class ActiveSession:
    """A best-effort extended-session context bound to one ECU address pair."""

    def __init__(self, transport, tx_id: int, rx_id: int, *, allow: bool,
                 timeout: float = 1.0):
        assert_active_allowed(allow)
        self.transport = transport
        self.tx_id = tx_id
        self.rx_id = rx_id
        self.timeout = timeout
        self.opened = False

    def _req(self, pdu: bytes, timeout: Optional[float] = None) -> Optional[bytes]:
        return self.transport.request(self.tx_id, self.rx_id, bytes(pdu),
                                      timeout=timeout or self.timeout)

    def open(self, session_type: int = EXTENDED_SESSION) -> dict:
        """Try to open a session. Returns a result dict (never raises)."""
        r = self._req([0x10, session_type])
        if r is None:
            return {"opened": False, "result": "no_response"}
        if r[0] == 0x7F:
            return {"opened": False, "result": "negative",
                    "nrc": nrc_name(r[2] if len(r) > 2 else 0)}
        self.opened = r[0] == 0x50
        return {"opened": self.opened, "result": "positive", "raw": r.hex()}

    def tester_present(self) -> None:
        self._req([0x3E, 0x80], timeout=0.3)  # 0x80: suppress positive response

    def read_did(self, did: int) -> Optional[bytes]:
        self.tester_present()  # keep the session alive between reads
        r = self._req([0x22, (did >> 8) & 0xFF, did & 0xFF], timeout=0.5)
        if r and r[0] == 0x62 and (r[1] << 8 | r[2]) == did:
            return r[3:]
        return None

    def close(self) -> None:
        try:
            self._req([0x10, DEFAULT_SESSION], timeout=0.3)
        except Exception:
            pass


def probe_extended_session(transport, ecus, *, allow: bool,
                           dids: Optional[List[int]] = None,
                           timeout: float = 1.0) -> List[dict]:
    """For each ``(tx, rx)`` ECU, try an extended session and report the outcome.

    ``ecus`` is a list of ``(tx_id, rx_id)``. Returns one result dict per ECU
    describing whether the session opened and any DIDs it exposed.
    """
    assert_active_allowed(allow)
    dids = dids or []
    out: List[dict] = []
    for tx, rx in ecus:
        sess = ActiveSession(transport, tx, rx, allow=allow, timeout=timeout)
        info = {"tx_id": f"0x{tx:03X}" if tx <= 0x7FF else f"0x{tx:08X}",
                "rx_id": f"0x{rx:03X}" if rx <= 0x7FF else f"0x{rx:08X}"}
        info["session"] = sess.open(EXTENDED_SESSION)
        unlocked = {}
        if sess.opened:
            for did in dids:
                v = sess.read_did(did)
                if v is not None:
                    unlocked[f"0x{did:04X}"] = (
                        v.decode("ascii") if v and all(0x20 <= b < 0x7F for b in v)
                        else v.hex())
            sess.close()
        info["unlocked_dids"] = unlocked
        out.append(info)
        time.sleep(0.05)
    return out
