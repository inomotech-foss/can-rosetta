"""OBD-II catalog and decoding.

A small but real catalog of standardized mode-01 PIDs plus the machinery to
read and decode the *supported-PID* bitmasks, so we can enumerate exactly which
standardized PIDs a given vehicle exposes.

Only read-style OBD modes are ever issued: service ``0x01`` (current data) and
``0x09`` (vehicle info). :func:`assert_read_only_mode` enforces this.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .transport import OBD_FUNCTIONAL_TX, OBD_RESP_BASE, Transport

# Read-style OBD modes permitted by SAFETY.md. Nothing else may be sent.
SAFE_OBD_MODES = frozenset({0x01, 0x09})


def assert_read_only_mode(mode: int) -> None:
    """Refuse any OBD service that is not a safe, read-style mode."""
    if mode not in SAFE_OBD_MODES:
        raise ValueError(
            f"OBD mode 0x{mode:02X} is not a permitted read-only service "
            f"(allowed: {sorted(hex(m) for m in SAFE_OBD_MODES)})"
        )


@dataclass(frozen=True)
class Pid:
    pid: int
    name: str
    length: int  # number of data bytes expected
    unit: str
    decode: Callable[[bytes], float]

    @property
    def hex(self) -> str:
        return f"0x{self.pid:02X}"


def _A(d: bytes) -> int:
    return d[0]


def _B(d: bytes) -> int:
    return d[1]


# Standard mode-01 PID catalog. Formulas per SAE J1979.
PIDS: Dict[int, Pid] = {
    0x04: Pid(0x04, "engine_load", 1, "%", lambda d: _A(d) * 100.0 / 255.0),
    0x05: Pid(0x05, "coolant_temp", 1, "degC", lambda d: _A(d) - 40.0),
    0x0C: Pid(0x0C, "engine_rpm", 2, "rpm", lambda d: (256 * _A(d) + _B(d)) / 4.0),
    0x0D: Pid(0x0D, "vehicle_speed", 1, "km/h", lambda d: float(_A(d))),
    0x0F: Pid(0x0F, "intake_temp", 1, "degC", lambda d: _A(d) - 40.0),
    0x10: Pid(0x10, "maf", 2, "g/s", lambda d: (256 * _A(d) + _B(d)) / 100.0),
    0x11: Pid(0x11, "throttle_pos", 1, "%", lambda d: _A(d) * 100.0 / 255.0),
    0x2F: Pid(0x2F, "fuel_level", 1, "%", lambda d: _A(d) * 100.0 / 255.0),
    0x42: Pid(0x42, "control_module_voltage", 2, "V",
              lambda d: (256 * _A(d) + _B(d)) / 1000.0),
    0x46: Pid(0x46, "ambient_temp", 1, "degC", lambda d: _A(d) - 40.0),
    # EV / hybrid: standardized SoC-style PID. Most EV battery detail is
    # manufacturer-specific UDS (read via 0x22), but this one is standard.
    0x5B: Pid(0x5B, "hybrid_battery_remaining", 1, "%", lambda d: _A(d) * 100.0 / 255.0),
}

# PIDs whose only purpose is to advertise which other PIDs are supported.
SUPPORT_QUERY_PIDS = (0x00, 0x20, 0x40, 0x60, 0x80, 0xA0, 0xC0, 0xE0)


def pid_hex(pid: int) -> str:
    return f"0x{pid:02X}"


def parse_supported_pids(base_pid: int, data: bytes) -> List[int]:
    """Decode a 4-byte supported-PID bitmask into a list of PID numbers.

    ``base_pid`` is the query PID (0x00, 0x20, ...); the four bytes describe
    support for PIDs ``base_pid+1 .. base_pid+0x20``. The most-significant bit
    of the first byte is ``base_pid+1``.
    """
    supported: List[int] = []
    if len(data) < 4:
        return supported
    bits = int.from_bytes(data[:4], "big")
    for i in range(32):
        if bits & (1 << (31 - i)):
            supported.append(base_pid + 1 + i)
    return supported


class ObdClient:
    """Thin OBD-II client over a :class:`Transport`."""

    def __init__(self, transport: Transport,
                 tx_id: int = OBD_FUNCTIONAL_TX, rx_id: int = OBD_RESP_BASE,
                 timeout: float = 1.0):
        self.transport = transport
        self.tx_id = tx_id
        self.rx_id = rx_id
        self.timeout = timeout

    def _request(self, mode: int, pid: Optional[int]) -> Optional[bytes]:
        assert_read_only_mode(mode)  # hard safety guard
        payload = bytes([mode]) if pid is None else bytes([mode, pid])
        resp = self.transport.request(self.tx_id, self.rx_id, payload, self.timeout)
        if not resp:
            return None
        # Positive response echoes mode+0x40 then the pid.
        if resp[0] != (mode + 0x40):
            return None
        return resp

    def query_raw(self, pid: int, mode: int = 0x01) -> Optional[bytes]:
        """Return the raw data bytes for ``pid`` (after the mode+pid echo)."""
        resp = self._request(mode, pid)
        if resp is None or len(resp) < 2:
            return None
        return resp[2:]

    def enumerate_supported_pids(self) -> List[int]:
        """Walk the 0x00/0x20/... bitmasks to list all supported mode-01 PIDs."""
        found: List[int] = []
        for base in SUPPORT_QUERY_PIDS:
            data = self.query_raw(base, mode=0x01)
            if data is None:
                break
            block = parse_supported_pids(base, data)
            # Drop the pure "next block exists" marker pids from the result.
            found.extend(p for p in block if p not in SUPPORT_QUERY_PIDS)
            # The continuation marker is (base + 0x20); stop if it's absent.
            if (base + 0x20) not in parse_supported_pids(base, data):
                break
        return sorted(set(found))

    def sample_pid(self, pid: int) -> Optional[dict]:
        """Read one PID and return a discovery sample dict (or None)."""
        data = self.query_raw(pid, mode=0x01)
        if data is None:
            return None
        catalog = PIDS.get(pid)
        sample = {
            "mode": 1,
            "pid": pid_hex(pid),
            "raw": data.hex(),
        }
        if catalog is not None and len(data) >= catalog.length:
            try:
                sample["name"] = catalog.name
                sample["value"] = round(float(catalog.decode(data)), 4)
                sample["unit"] = catalog.unit
            except Exception:
                sample["value"] = None
        else:
            sample["value"] = None
        return sample
