"""Transport abstraction for the CAN-Rosetta edge component.

A :class:`Transport` is the single seam between the discovery/logging logic and
whatever physically talks to the vehicle bus. Three implementations ship:

* :class:`SocketCanTransport` -- ``python-can`` / SocketCAN (Linux, AutoPi).
* :class:`ElmTransport`       -- ELM327 / STN serial dongles via ``pyserial``.
* :class:`SimulatedTransport` -- an in-process fake vehicle bus for tests/demos.

All optional hardware dependencies are imported lazily so that importing this
module (and running the test-suite) needs nothing but the standard library.
"""

from __future__ import annotations

import abc
import time
from dataclasses import dataclass, field
from typing import Iterator, Optional

# Standard OBD/UDS addressing (11-bit).
OBD_FUNCTIONAL_TX = 0x7DF
OBD_PHYSICAL_TX_BASE = 0x7E0  # 0x7E0 .. 0x7E7
OBD_RESP_BASE = 0x7E8         # 0x7E8 .. 0x7EF


@dataclass
class Frame:
    """A single CAN frame, in the shape the logger persists.

    ``to_record`` produces a dict that validates against
    ``schemas/can_frame.record.schema.json``.
    """

    arb_id: int
    data: bytes
    is_extended: bool = False
    channel: str = "can0"
    direction: str = "rx"
    probe_id: Optional[str] = None
    t_mono: float = field(default_factory=time.monotonic)
    t_utc: float = field(default_factory=time.time)

    @property
    def dlc(self) -> int:
        return len(self.data)

    def to_record(self) -> dict:
        return {
            "t_mono": float(self.t_mono),
            "t_utc": float(self.t_utc),
            "channel": self.channel,
            "arb_id": int(self.arb_id),
            "is_extended": bool(self.is_extended),
            "dlc": int(self.dlc),
            "data": self.data.hex(),
            "direction": self.direction,
            "probe_id": self.probe_id,
        }


class Transport(abc.ABC):
    """Abstract vehicle-bus transport."""

    channel: str = "can0"

    def open(self) -> "Transport":  # pragma: no cover - trivial default
        return self

    def close(self) -> None:  # pragma: no cover - trivial default
        pass

    def __enter__(self) -> "Transport":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()

    @abc.abstractmethod
    def send_frame(self, arb_id: int, data: bytes, is_extended: bool = False) -> None:
        """Transmit a single raw CAN frame."""

    @abc.abstractmethod
    def recv_frames(self, timeout: float) -> Iterator[Frame]:
        """Yield every frame observed within ``timeout`` seconds, then stop."""

    @abc.abstractmethod
    def request(
        self,
        tx_id: int,
        rx_id: int,
        payload: bytes,
        timeout: float = 1.0,
    ) -> Optional[bytes]:
        """ISO-TP style request/response.

        Sends ``payload`` (a UDS/OBD service PDU, without ISO-TP framing) to
        ``tx_id`` and returns the reassembled response PDU seen on ``rx_id``,
        or ``None`` on timeout / negative response.
        """


# --------------------------------------------------------------------------- #
# SocketCAN (python-can)
# --------------------------------------------------------------------------- #
class SocketCanTransport(Transport):
    """SocketCAN transport backed by ``python-can``.

    ``python-can`` is imported lazily; install the ``[can]`` extra to use it.
    """

    def __init__(self, channel: str = "can0", bitrate: int = 500_000,
                 interface: str = "socketcan"):
        self.channel = channel
        self.bitrate = bitrate
        self.interface = interface
        self._bus = None

    def open(self) -> "SocketCanTransport":
        import can  # lazy

        self._bus = can.Bus(
            channel=self.channel, interface=self.interface, bitrate=self.bitrate
        )
        return self

    def close(self) -> None:
        if self._bus is not None:
            self._bus.shutdown()
            self._bus = None

    def _bus_or_raise(self):
        if self._bus is None:
            raise RuntimeError("SocketCanTransport not open(); call open() first")
        return self._bus

    def send_frame(self, arb_id: int, data: bytes, is_extended: bool = False) -> None:
        import can  # lazy

        bus = self._bus_or_raise()
        bus.send(
            can.Message(
                arbitration_id=arb_id,
                data=bytes(data),
                is_extended_id=is_extended,
            )
        )

    def recv_frames(self, timeout: float) -> Iterator[Frame]:
        bus = self._bus_or_raise()
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            msg = bus.recv(timeout=remaining)
            if msg is None:
                return
            yield Frame(
                arb_id=msg.arbitration_id,
                data=bytes(msg.data),
                is_extended=bool(msg.is_extended_id),
                channel=self.channel,
                direction="rx",
                t_utc=time.time(),
            )

    def request(self, tx_id, rx_id, payload, timeout=1.0):
        """Best-effort ISO-TP single-frame request + response reassembly."""
        bus = self._bus_or_raise()
        payload = bytes(payload)
        if len(payload) > 7:
            raise NotImplementedError("multi-frame ISO-TP TX not supported")
        # Single frame: high nibble 0 == SF, low nibble == length.
        sf = bytes([len(payload)]) + payload
        sf = sf + b"\x00" * (8 - len(sf))
        self.send_frame(tx_id, sf)
        return self._recv_isotp(bus, rx_id, timeout)

    def _recv_isotp(self, bus, rx_id, timeout) -> Optional[bytes]:
        deadline = time.monotonic() + timeout
        expected = 0
        buf = bytearray()
        while time.monotonic() < deadline:
            msg = bus.recv(timeout=deadline - time.monotonic())
            if msg is None or msg.arbitration_id != rx_id:
                continue
            d = bytes(msg.data)
            pci = d[0] >> 4
            if pci == 0x0:  # single frame
                n = d[0] & 0x0F
                return d[1 : 1 + n]
            if pci == 0x1:  # first frame
                expected = ((d[0] & 0x0F) << 8) | d[1]
                buf.extend(d[2:8])
                # send flow control (clear-to-send). This is the one frame we
                # must emit to receive multi-frame reads; it is not a write to
                # the vehicle, only ISO-TP handshaking.
                self.send_frame(rx_id - 8, b"\x30\x00\x00")
            elif pci == 0x2:  # consecutive frame
                buf.extend(d[1:8])
                if len(buf) >= expected:
                    return bytes(buf[:expected])
        return None


# --------------------------------------------------------------------------- #
# ELM327 / STN over serial (pyserial)
# --------------------------------------------------------------------------- #
class ElmTransport(Transport):
    """ELM327 / STN1110 serial transport (best-effort).

    ``pyserial`` is imported lazily; install the ``[elm]`` extra. ELM adapters
    expose OBD/UDS request-response cleanly but are poor at raw promiscuous
    sniffing, so ``recv_frames`` uses the monitor-all (``AT MA``) mode which is
    documented as best-effort.
    """

    def __init__(self, port: str = "/dev/ttyUSB0", baudrate: int = 115200,
                 channel: str = "obd"):
        self.port = port
        self.baudrate = baudrate
        self.channel = channel
        self._ser = None

    def open(self) -> "ElmTransport":
        import serial  # lazy

        self._ser = serial.Serial(self.port, self.baudrate, timeout=1)
        for cmd in ("ATZ", "ATE0", "ATL0", "ATS0", "ATH1", "ATSP0"):
            self._cmd(cmd)
        return self

    def close(self) -> None:
        if self._ser is not None:
            self._ser.close()
            self._ser = None

    def _ser_or_raise(self):
        if self._ser is None:
            raise RuntimeError("ElmTransport not open(); call open() first")
        return self._ser

    def _cmd(self, s: str, timeout: float = 1.0) -> str:
        ser = self._ser_or_raise()
        ser.reset_input_buffer()
        ser.write((s + "\r").encode("ascii"))
        deadline = time.monotonic() + timeout
        out = bytearray()
        while time.monotonic() < deadline:
            chunk = ser.read(64)
            if chunk:
                out.extend(chunk)
                if b">" in out:  # ELM prompt terminator
                    break
        return out.decode("ascii", "replace")

    def send_frame(self, arb_id: int, data: bytes, is_extended: bool = False) -> None:
        # Set the header then transmit the data bytes. Used only for
        # request/response addressing, never for writing to vehicle ECUs.
        self._cmd(f"ATSH{arb_id:03X}")
        self._cmd(bytes(data).hex().upper())

    def recv_frames(self, timeout: float) -> Iterator[Frame]:
        ser = self._ser_or_raise()
        ser.write(b"ATMA\r")  # monitor all
        deadline = time.monotonic() + timeout
        buf = bytearray()
        try:
            while time.monotonic() < deadline:
                chunk = ser.read(128)
                if not chunk:
                    continue
                buf.extend(chunk)
                *lines, buf = self._split_lines(buf)
                for line in lines:
                    frame = self._parse_monitor_line(line)
                    if frame is not None:
                        yield frame
        finally:
            self._cmd("")  # any char stops AT MA

    @staticmethod
    def _split_lines(buf: bytearray):
        parts = buf.split(b"\r")
        return parts

    def _parse_monitor_line(self, line: bytes) -> Optional[Frame]:
        text = line.decode("ascii", "replace").strip().replace(" ", "")
        if not text or not all(c in "0123456789ABCDEFabcdef" for c in text):
            return None
        # AT H1 format: <3 hex id><data...>
        if len(text) < 3 or len(text) % 2 == 1:
            return None
        try:
            arb = int(text[:3], 16)
            data = bytes.fromhex(text[3:])
        except ValueError:
            return None
        return Frame(arb_id=arb, data=data, channel=self.channel,
                     direction="rx", t_utc=time.time())

    def request(self, tx_id, rx_id, payload, timeout=1.0):
        self._cmd(f"ATSH{tx_id:03X}")
        self._cmd(f"ATCRA{rx_id:03X}")  # only accept responses from rx_id
        resp = self._cmd(bytes(payload).hex().upper(), timeout=timeout)
        return self._parse_response(resp, rx_id)

    @staticmethod
    def _parse_response(resp: str, rx_id: int) -> Optional[bytes]:
        out = bytearray()
        for line in resp.splitlines():
            t = line.strip().replace(" ", "")
            if not t or any(c not in "0123456789ABCDEFabcdef" for c in t):
                continue
            if len(t) % 2 == 1:
                continue
            raw = bytes.fromhex(t)
            # Strip the CAN id header (AT H1 is on) if present.
            if len(raw) >= 2 and ((raw[0] << 8 | raw[1]) & 0x7FF) == (rx_id & 0x7FF):
                raw = raw[2:]
            if raw:
                # Strip a single-frame ISO-TP PCI byte if it looks like one.
                if raw and (raw[0] >> 4) == 0 and (raw[0] & 0x0F) <= len(raw) - 1:
                    n = raw[0] & 0x0F
                    out.extend(raw[1 : 1 + n])
                else:
                    out.extend(raw)
        return bytes(out) or None


# --------------------------------------------------------------------------- #
# Simulated bus (no hardware) -- the important one for tests
# --------------------------------------------------------------------------- #
class _PeriodicSource:
    def __init__(self, arb_id: int, period: float, builder):
        self.arb_id = arb_id
        self.period = period
        self.builder = builder
        self.next_t = 0.0
        self.counter = 0


class SimulatedTransport(Transport):
    """An in-process fake vehicle bus.

    * Emits periodic plain-CAN frames whose bytes encode a rising/falling
      vehicle speed and RPM (0x3E9 speed, 0x3EA rpm, 0x100 constant).
    * Answers OBD mode-01 requests for a handful of PIDs and mode-09.
    * Answers a UDS ReadDataByIdentifier for the VIN and a couple of DIDs.

    Speed follows a triangle wave over ``period_s`` seconds; RPM is derived
    from speed so the two correlate, exactly as they would in a real car.
    """

    VIN = "WVWZZZ1KZAW000001"
    ECU_SERIAL = "ECU-SN-0001"
    SW_VERSION = "SW 01.23.45"

    # PIDs the simulated ECU reports as supported (mode 01).
    SUPPORTED_PIDS = (0x05, 0x0C, 0x0D, 0x11)
    # DIDs the simulated ECU answers (UDS 0x22).
    SUPPORTED_DIDS = {0xF190: VIN, 0xF18C: ECU_SERIAL, 0xF195: SW_VERSION}

    def __init__(self, channel: str = "can0", speed_period_s: float = 20.0):
        self.channel = channel
        self.speed_period_s = speed_period_s
        self._t0 = time.monotonic()
        self._sources = [
            _PeriodicSource(0x3E9, 0.10, self._build_speed_frame),
            _PeriodicSource(0x3EA, 0.05, self._build_rpm_frame),
            _PeriodicSource(0x100, 0.20, self._build_const_frame),
        ]

    def open(self) -> "SimulatedTransport":
        now = time.monotonic()
        self._t0 = now
        for s in self._sources:
            s.next_t = now + s.period
        return self

    # -- physical model ---------------------------------------------------- #
    def _state(self):
        """Return (speed_kmh, rpm, coolant_c) at the current instant."""
        t = time.monotonic() - self._t0
        phase = (t % self.speed_period_s) / self.speed_period_s
        # triangle wave 0..120..0 km/h
        speed = 240.0 * phase if phase < 0.5 else 240.0 * (1.0 - phase)
        rpm = 800.0 + speed * 45.0        # idle + gear-ratio-ish coupling
        coolant = 80.0 + 10.0 * phase     # warms slowly
        return speed, rpm, coolant

    # -- periodic frame builders ------------------------------------------- #
    def _build_speed_frame(self, src: _PeriodicSource) -> bytes:
        speed, _, _ = self._state()
        raw = int(round(speed * 100)) & 0xFFFF  # 0.01 km/h resolution
        src.counter = (src.counter + 1) & 0xFF
        return bytes([0x00, raw >> 8, raw & 0xFF, src.counter, 0, 0, 0, 0])

    def _build_rpm_frame(self, src: _PeriodicSource) -> bytes:
        _, rpm, _ = self._state()
        raw = int(round(rpm)) & 0xFFFF
        src.counter = (src.counter + 1) & 0xFF
        return bytes([0x00, 0x00, 0x00, raw >> 8, raw & 0xFF, src.counter, 0, 0])

    def _build_const_frame(self, src: _PeriodicSource) -> bytes:
        return bytes([0xDE, 0xAD, 0xBE, 0xEF, 0x00, 0x00, 0x00, 0x00])

    # -- Transport API ----------------------------------------------------- #
    def send_frame(self, arb_id: int, data: bytes, is_extended: bool = False) -> None:
        # A simulated bus has nowhere to put a raw injected frame; accept and
        # drop it. request() is the meaningful TX path.
        return None

    def recv_frames(self, timeout: float) -> Iterator[Frame]:
        deadline = time.monotonic() + timeout
        while True:
            src = min(self._sources, key=lambda s: s.next_t)
            now = time.monotonic()
            if src.next_t > deadline:
                remaining = deadline - now
                if remaining > 0:
                    time.sleep(remaining)
                return
            wait = src.next_t - now
            if wait > 0:
                time.sleep(wait)
            data = src.builder(src)
            src.next_t += src.period
            yield Frame(
                arb_id=src.arb_id,
                data=data,
                channel=self.channel,
                direction="rx",
                t_utc=time.time(),
            )

    def request(self, tx_id, rx_id, payload, timeout=1.0):
        payload = bytes(payload)
        if not payload:
            return None
        # Only the functional/physical addresses the sim ECU listens on.
        if tx_id not in (OBD_FUNCTIONAL_TX, OBD_PHYSICAL_TX_BASE):
            return None
        if rx_id != OBD_RESP_BASE:
            return None
        service = payload[0]
        if service == 0x01:
            return self._obd_mode01(payload)
        if service == 0x09:
            return self._obd_mode09(payload)
        if service == 0x22:
            return self._uds_rdbi(payload)
        return None  # unsupported service -> no response

    # -- OBD/UDS responders ------------------------------------------------ #
    def _obd_mode01(self, payload: bytes) -> Optional[bytes]:
        if len(payload) < 2:
            return None
        pid = payload[1]
        speed, rpm, coolant = self._state()
        if pid in (0x00, 0x20, 0x40, 0x60, 0x80, 0xA0, 0xC0, 0xE0):
            return bytes([0x41, pid]) + self._supported_bitmask(pid)
        if pid not in self.SUPPORTED_PIDS:
            return None
        if pid == 0x05:  # coolant temp: A-40
            return bytes([0x41, pid, int(round(coolant)) + 40])
        if pid == 0x0C:  # rpm: (256A+B)/4
            raw = int(round(rpm * 4)) & 0xFFFF
            return bytes([0x41, pid, raw >> 8, raw & 0xFF])
        if pid == 0x0D:  # speed: A km/h
            return bytes([0x41, pid, int(round(speed)) & 0xFF])
        if pid == 0x11:  # throttle: 100/255*A
            return bytes([0x41, pid, 60])
        return None

    def _supported_bitmask(self, base: int) -> bytes:
        bits = 0
        for pid in self.SUPPORTED_PIDS:
            if base < pid <= base + 0x20:
                bits |= 1 << (0x20 - (pid - base))
        # continuation bit (does the *next* block exist?)
        if any(p > base + 0x20 for p in self.SUPPORTED_PIDS):
            bits |= 1  # bit for pid (base+0x20) signals next-block support
        return bits.to_bytes(4, "big")

    def _obd_mode09(self, payload: bytes) -> Optional[bytes]:
        if len(payload) < 2:
            return None
        pid = payload[1]
        if pid == 0x00:
            # supported: only 0x02 (VIN) advertised
            return bytes([0x49, 0x00]) + (1 << 30).to_bytes(4, "big")
        if pid == 0x02:  # VIN, 1 data item
            return bytes([0x49, 0x02, 0x01]) + self.VIN.encode("ascii")
        return None

    def _uds_rdbi(self, payload: bytes) -> Optional[bytes]:
        if len(payload) < 3:
            return None
        did = (payload[1] << 8) | payload[2]
        value = self.SUPPORTED_DIDS.get(did)
        if value is None:
            # negative response: service not supported for this DID
            return bytes([0x7F, 0x22, 0x31])
        return bytes([0x62, payload[1], payload[2]]) + value.encode("ascii")
