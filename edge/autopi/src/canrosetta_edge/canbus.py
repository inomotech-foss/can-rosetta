"""Native SocketCAN access + CAN bitrate/interface auto-detection.

This module talks to the kernel's SocketCAN stack directly through the standard
library ``socket`` (``AF_CAN`` / ``SOCK_RAW``). It needs **no** third-party
package -- in particular no ``python-can`` -- which matters on a stock AutoPi
where ``pip`` may be unavailable but a SocketCAN interface (``can0``) is already
present.

It provides two things:

* :class:`NativeCanBus` -- a tiny raw-CAN socket (send/recv classic 2.0 frames,
  optional error-frame reception).
* Interface + bitrate discovery (:func:`detect_bitrate`, :func:`find_active_bus`)
  -- because a vehicle bus that answers on the OBD port is not always 500 kbit/s,
  and an AutoPi has more than one CAN controller. Detection is **passive**: it
  configures the controller *listen-only* (it never transmits, never ACKs) and
  counts how many valid frames arrive at each candidate bitrate.

Reconfiguring a controller's bitrate uses ``ip link`` and therefore needs root
(``sudo``); all such calls degrade gracefully when they are not permitted, so a
non-root caller can still use an already-up interface.
"""

from __future__ import annotations

import errno
import shutil
import socket
import struct
import subprocess
import time
from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Sequence, Tuple

# --- SocketCAN constants (from <linux/can.h>) -------------------------------- #
CAN_EFF_FLAG = 0x80000000  # extended (29-bit) frame
CAN_RTR_FLAG = 0x40000000  # remote-transmission request
CAN_ERR_FLAG = 0x20000000  # error frame
CAN_SFF_MASK = 0x000007FF  # 11-bit id mask
CAN_EFF_MASK = 0x1FFFFFFF  # 29-bit id mask
CAN_ERR_MASK = 0x1FFFFFFF

SOL_CAN_RAW = 101
CAN_RAW_FILTER = 1
CAN_RAW_ERR_FILTER = 2

# struct can_frame { canid_t can_id; u8 len; u8 pad; u8 res0; u8 res1; u8 data[8]; }
_CAN_FRAME_FMT = "=IB3x8s"
_CAN_FRAME_SIZE = struct.calcsize(_CAN_FRAME_FMT)  # 16

# Candidate bitrates to probe, most-likely first (OBD/HS-CAN is almost always
# 500k; powertrain/comfort sub-buses are commonly 250k/125k).
DEFAULT_BITRATES: Tuple[int, ...] = (500_000, 250_000, 125_000, 1_000_000, 100_000, 83_333)


@dataclass
class RawFrame:
    """A raw CAN frame as read from the socket (no persistence concerns)."""

    arb_id: int
    data: bytes
    is_extended: bool = False
    is_error: bool = False
    is_rtr: bool = False
    t_mono: float = 0.0
    t_utc: float = 0.0


class NativeCanBus:
    """A raw SocketCAN socket over one interface, using only the stdlib."""

    def __init__(self, interface: str = "can0", receive_errors: bool = False):
        self.interface = interface
        self.receive_errors = receive_errors
        self._sock: Optional[socket.socket] = None

    # -- lifecycle --------------------------------------------------------- #
    def open(self) -> "NativeCanBus":
        sock = socket.socket(socket.PF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        # A vehicle bus can push ~1000 frames/s; a small RX buffer overflows and
        # silently drops frames -- including a diagnostic response we are waiting
        # for. Enlarge it so bursts survive between reads.
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        except OSError:
            pass
        if self.receive_errors:
            # Ask the kernel to also deliver error frames so a wrong-bitrate /
            # bus-off condition is observable during detection.
            sock.setsockopt(SOL_CAN_RAW, CAN_RAW_ERR_FILTER,
                            struct.pack("=I", CAN_ERR_MASK))
        sock.bind((self.interface,))
        self._sock = sock
        return self

    # -- kernel receive filters ------------------------------------------- #
    def set_filters(self, filters) -> None:
        """Install kernel CAN_RAW filters: list of ``(can_id, can_mask)``.

        Filtering in the kernel (rather than in Python) is what makes
        request/response reliable on a busy bus: the socket then only ever
        receives the handful of diagnostic response ids, so the 1000 fps of
        broadcast traffic can never crowd the response out of the RX buffer.
        An empty list means *receive nothing*; use :meth:`receive_all` to reset.
        """
        sock = self._sock_or_raise()
        blob = b"".join(struct.pack("=II", cid & 0xFFFFFFFF, m & 0xFFFFFFFF)
                        for cid, m in filters)
        sock.setsockopt(SOL_CAN_RAW, CAN_RAW_FILTER, blob)

    def receive_all(self) -> None:
        """Reset to receiving every frame (id 0, mask 0 matches all)."""
        self.set_filters([(0, 0)])

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def __enter__(self) -> "NativeCanBus":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()

    def _sock_or_raise(self) -> socket.socket:
        if self._sock is None:
            raise RuntimeError("NativeCanBus not open(); call open() first")
        return self._sock

    # -- I/O --------------------------------------------------------------- #
    def send(self, arb_id: int, data: bytes, is_extended: Optional[bool] = None) -> None:
        """Transmit one classic CAN frame (<= 8 data bytes)."""
        sock = self._sock_or_raise()
        data = bytes(data)
        if len(data) > 8:
            raise ValueError("classic CAN frame data must be <= 8 bytes")
        if is_extended is None:
            is_extended = arb_id > CAN_SFF_MASK
        can_id = (arb_id & CAN_EFF_MASK) | (CAN_EFF_FLAG if is_extended else 0)
        payload = data.ljust(8, b"\x00")
        sock.send(struct.pack(_CAN_FRAME_FMT, can_id, len(data), payload))

    def recv(self, timeout: float) -> Optional[RawFrame]:
        """Return the next frame within ``timeout`` seconds, or ``None``."""
        sock = self._sock_or_raise()
        sock.settimeout(max(0.0, timeout))
        try:
            buf = sock.recv(_CAN_FRAME_SIZE)
        except socket.timeout:
            return None
        except OSError as exc:  # e.g. ENETDOWN if the link goes down mid-read
            if exc.errno in (errno.ENETDOWN, errno.ENODEV):
                return None
            raise
        if len(buf) < _CAN_FRAME_SIZE:
            return None
        can_id, dlc, payload = struct.unpack(_CAN_FRAME_FMT, buf)
        is_ext = bool(can_id & CAN_EFF_FLAG)
        mask = CAN_EFF_MASK if is_ext else CAN_SFF_MASK
        now = time.monotonic()
        return RawFrame(
            arb_id=can_id & mask,
            data=payload[:dlc],
            is_extended=is_ext,
            is_error=bool(can_id & CAN_ERR_FLAG),
            is_rtr=bool(can_id & CAN_RTR_FLAG),
            t_mono=now,
            t_utc=time.time(),
        )

    def recv_until(self, deadline: float) -> Iterator[RawFrame]:
        """Yield frames until ``time.monotonic() >= deadline``."""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            frame = self.recv(min(remaining, 0.5))
            if frame is not None:
                yield frame


# --------------------------------------------------------------------------- #
# Interface configuration (needs root; degrades gracefully)
# --------------------------------------------------------------------------- #
def _ip(*args: str, use_sudo: bool = True, timeout: float = 5.0) -> Tuple[int, str]:
    """Run ``ip <args>`` (optionally via sudo). Returns (rc, combined_output)."""
    ip_bin = shutil.which("ip") or "/sbin/ip"
    cmd: List[str] = []
    if use_sudo and shutil.which("sudo"):
        cmd += ["sudo", "-n"]
    cmd += [ip_bin, *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return 1, str(exc)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def list_can_interfaces() -> List[str]:
    """All CAN network interfaces the kernel knows about (up or down)."""
    rc, out = _ip("-o", "link", "show", "type", "can", use_sudo=False)
    if rc != 0:
        # Fallback: sysfs enumeration.
        import glob
        import os
        return sorted(os.path.basename(p) for p in glob.glob("/sys/class/net/can*"))
    names: List[str] = []
    for line in out.splitlines():
        # "6: can0: <...>" -> token "can0:"
        parts = line.split(":", 2)
        if len(parts) >= 2:
            names.append(parts[1].strip().split("@")[0])
    return names


def interface_is_up(interface: str) -> bool:
    rc, out = _ip("-br", "link", "show", interface, use_sudo=False)
    if rc != 0 or not out:
        return False
    # "-br" format: "can0   UP   <flags>"; the operstate is the 2nd column.
    cols = out.split()
    return len(cols) >= 2 and cols[1].upper() in ("UP", "UNKNOWN")


def configure_bitrate(interface: str, bitrate: int, listen_only: bool = True,
                      restart_ms: int = 100) -> bool:
    """Bring ``interface`` down, set ``bitrate`` (+ optional listen-only), up.

    Returns ``True`` on success. Needs root; returns ``False`` (without raising)
    when the ``ip`` calls are not permitted, so callers can fall back to using an
    already-configured interface.
    """
    _ip("link", "set", interface, "down")
    args = ["link", "set", interface, "type", "can", "bitrate", str(bitrate),
            "restart-ms", str(restart_ms)]
    if listen_only:
        args += ["listen-only", "on"]
    else:
        args += ["listen-only", "off"]
    rc, _ = _ip(*args)
    if rc != 0:
        return False
    rc, _ = _ip("link", "set", interface, "up")
    return rc == 0


def set_interface_down(interface: str) -> bool:
    rc, _ = _ip("link", "set", interface, "down")
    return rc == 0


# --------------------------------------------------------------------------- #
# Bitrate / active-bus detection
# --------------------------------------------------------------------------- #
@dataclass
class BitrateResult:
    interface: str
    bitrate: int
    frames: int
    errors: int
    unique_ids: int
    configured: bool  # did we (re)configure the controller, or read as-is?

    @property
    def score(self) -> float:
        # Valid traffic with few error frames is the signal of a matched bitrate.
        if self.frames == 0:
            return 0.0
        return self.frames - 5.0 * self.errors


@dataclass
class BusScan:
    interface: str
    bitrate: Optional[int]
    frames: int
    unique_ids: int
    used_existing: bool
    candidates: List[BitrateResult] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return self.bitrate is not None and self.frames > 0


def _count_traffic(interface: str, window_s: float) -> Tuple[int, int, int]:
    """Passively count (frames, errors, unique_ids) on an already-up interface."""
    frames = errors = 0
    ids = set()
    try:
        bus = NativeCanBus(interface, receive_errors=True).open()
    except OSError:
        return 0, 0, 0
    try:
        for fr in bus.recv_until(time.monotonic() + window_s):
            if fr.is_error:
                errors += 1
                continue
            frames += 1
            ids.add(fr.arb_id)
    finally:
        bus.close()
    return frames, errors, len(ids)


def detect_bitrate(interface: str,
                   bitrates: Sequence[int] = DEFAULT_BITRATES,
                   window_s: float = 2.0,
                   settle_s: float = 0.3,
                   allow_reconfigure: bool = True) -> BusScan:
    """Find the bitrate at which ``interface`` sees valid traffic.

    Strategy, cheapest first:

    1. If the interface is already up, sample it as-is (no root, no disturbance).
       If it already sees traffic, we are done -- never reconfigure a working,
       possibly in-use, controller.
    2. Otherwise probe each candidate bitrate in **listen-only** mode (passive,
       never transmits) and keep the one with the most valid frames.
    """
    results: List[BitrateResult] = []

    if interface_is_up(interface):
        frames, errors, uids = _count_traffic(interface, window_s)
        if frames > 0:
            br = _current_bitrate(interface)
            return BusScan(interface, br, frames, uids, used_existing=True,
                           candidates=[BitrateResult(interface, br or 0,
                                                     frames, errors, uids, False)])

    if not allow_reconfigure:
        return BusScan(interface, None, 0, 0, used_existing=False)

    for br in bitrates:
        if not configure_bitrate(interface, br, listen_only=True):
            # Cannot reconfigure (no root?) -> stop trying, report nothing.
            break
        time.sleep(settle_s)
        frames, errors, uids = _count_traffic(interface, window_s)
        results.append(BitrateResult(interface, br, frames, errors, uids, True))
        # A clean, busy bitrate is unambiguous; stop early.
        if frames >= 20 and errors == 0:
            break

    set_interface_down(interface)  # leave it down; caller will bring it up as needed

    if not results:
        return BusScan(interface, None, 0, 0, used_existing=False)

    best = max(results, key=lambda r: r.score)
    if best.frames == 0:
        return BusScan(interface, None, 0, 0, used_existing=False, candidates=results)
    return BusScan(interface, best.bitrate, best.frames, best.unique_ids,
                   used_existing=False, candidates=results)


def _current_bitrate(interface: str) -> Optional[int]:
    rc, out = _ip("-details", "link", "show", interface, use_sudo=False)
    if rc != 0:
        return None
    for tok in out.split():
        if tok.isdigit() and int(tok) in DEFAULT_BITRATES:
            return int(tok)
    # parse "bitrate 500000"
    parts = out.split()
    for i, tok in enumerate(parts):
        if tok == "bitrate" and i + 1 < len(parts) and parts[i + 1].isdigit():
            return int(parts[i + 1])
    return None


def find_active_bus(interfaces: Optional[Sequence[str]] = None,
                    bitrates: Sequence[int] = DEFAULT_BITRATES,
                    window_s: float = 2.0,
                    allow_reconfigure: bool = True) -> BusScan:
    """Probe interfaces (default: all CAN interfaces) and return the busiest.

    Prefers an interface that is already up and carrying traffic. This is the
    entry point the recon pipeline uses to answer "which bus, what speed?".
    """
    ifaces = list(interfaces) if interfaces else list_can_interfaces()
    if not ifaces:
        return BusScan("", None, 0, 0, used_existing=False)

    scans = [detect_bitrate(i, bitrates, window_s,
                            allow_reconfigure=allow_reconfigure) for i in ifaces]
    active = [s for s in scans if s.active]
    if active:
        return max(active, key=lambda s: s.frames)
    return max(scans, key=lambda s: s.frames)
