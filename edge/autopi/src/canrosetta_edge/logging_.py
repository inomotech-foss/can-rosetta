"""Stage 1b -- continuous logging.

Sniff every frame on the bus and append it to ``can/frames.parquet`` (via
``pyarrow``, imported lazily). When ``pyarrow`` is unavailable, transparently
fall back to a truly append-only ``can/frames.jsonl`` so tests run with no
optional dependencies.

Concurrently, an optional :class:`Poller` keeps sampling the discovered
OBD/UDS signals at a steady rate, both (a) appending decoded samples to a
reference series (destined for ``discovery.json``) and (b) emitting the probe
TX/RX frames into the log tagged with ``direction`` and ``probe_id``.
"""

from __future__ import annotations

import json
import os
import time
from typing import Callable, List, Optional

from .obd import ObdClient
from .transport import (
    OBD_FUNCTIONAL_TX,
    OBD_RESP_BASE,
    Frame,
    Transport,
)

# Columns persisted for each frame (order defines the parquet schema).
_COLUMNS = ("t_mono", "t_utc", "channel", "arb_id", "is_extended", "dlc",
            "data", "direction", "probe_id")


def _pyarrow_available() -> bool:
    try:
        import pyarrow  # noqa: F401
        import pyarrow.parquet  # noqa: F401
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Writers
# --------------------------------------------------------------------------- #
class FrameWriter:
    """Base class: append CAN frames to disk, tracking counts and time span."""

    def __init__(self, path: str):
        self.path = path
        self.count = 0
        self.t_start_utc: Optional[float] = None
        self.t_end_utc: Optional[float] = None

    def _track(self, rec: dict) -> None:
        self.count += 1
        t = rec.get("t_utc")
        if t is not None:
            if self.t_start_utc is None or t < self.t_start_utc:
                self.t_start_utc = t
            if self.t_end_utc is None or t > self.t_end_utc:
                self.t_end_utc = t

    def write(self, frame: Frame) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def flush(self) -> None:  # pragma: no cover - optional
        pass

    def close(self) -> None:  # pragma: no cover - optional
        pass

    def __enter__(self) -> "FrameWriter":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class JsonlFrameWriter(FrameWriter):
    """Truly append-only JSONL writer -- resumable across restarts."""

    def __init__(self, path: str):
        super().__init__(path)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        if os.path.exists(path):
            # Resume: account for the rows already on disk.
            with open(path, "r", encoding="utf-8") as fh:
                for _ in fh:
                    self.count += 1
        self._fh = open(path, "a", encoding="utf-8")

    def write(self, frame: Frame) -> None:
        rec = frame.to_record()
        self._fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
        self._track(rec)

    def flush(self) -> None:
        self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.flush()
        finally:
            self._fh.close()


class ParquetFrameWriter(FrameWriter):
    """Streaming Parquet writer with periodic row-group flushes.

    Resumable: if ``path`` already exists it is read back and re-emitted as the
    first row group, so a restart continues the same single file. Rows are
    buffered and flushed every ``batch_rows`` frames, bounding data loss on a
    power cut to at most one un-flushed batch.
    """

    def __init__(self, path: str, batch_rows: int = 2000):
        super().__init__(path)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        import pyarrow as pa
        import pyarrow.parquet as pq

        self._pa = pa
        self._pq = pq
        self._schema = pa.schema([
            ("t_mono", pa.float64()),
            ("t_utc", pa.float64()),
            ("channel", pa.string()),
            ("arb_id", pa.uint32()),
            ("is_extended", pa.bool_()),
            ("dlc", pa.uint8()),
            ("data", pa.binary()),
            ("direction", pa.string()),
            ("probe_id", pa.string()),
        ])
        self.batch_rows = batch_rows
        self._buf: List[dict] = []

        prior = None
        if os.path.exists(path):
            prior = pq.read_table(path)
            self.count += prior.num_rows
            os.remove(path)
        self._writer = pq.ParquetWriter(path, self._schema)
        if prior is not None and prior.num_rows:
            self._writer.write_table(prior.cast(self._schema))

    def write(self, frame: Frame) -> None:
        rec = frame.to_record()
        # Store raw bytes (not hex) in the binary column.
        row = dict(rec)
        row["data"] = frame.data
        self._buf.append(row)
        self._track(rec)
        if len(self._buf) >= self.batch_rows:
            self._flush_buffer()

    def _flush_buffer(self) -> None:
        if not self._buf:
            return
        cols = {name: [r[name] for r in self._buf] for name in _COLUMNS}
        table = self._pa.table(cols, schema=self._schema)
        self._writer.write_table(table)
        self._buf.clear()

    def flush(self) -> None:
        self._flush_buffer()

    def close(self) -> None:
        self._flush_buffer()
        self._writer.close()


def make_frame_writer(path_no_ext: str, prefer_parquet: bool = True) -> FrameWriter:
    """Create the best available writer for ``<path_no_ext>.{parquet,jsonl}``."""
    if prefer_parquet and _pyarrow_available():
        return ParquetFrameWriter(path_no_ext + ".parquet")
    return JsonlFrameWriter(path_no_ext + ".jsonl")


# --------------------------------------------------------------------------- #
# Poller -- dense OBD/UDS reference series during logging
# --------------------------------------------------------------------------- #
class Poller:
    """Polls a set of OBD PIDs at a steady rate.

    Each poll appends a decoded sample and yields the induced TX/RX frames so
    they land in the log tagged with ``direction`` and ``probe_id``.
    """

    def __init__(self, transport: Transport, pids: List[int],
                 tx_id: int = OBD_FUNCTIONAL_TX, rx_id: int = OBD_RESP_BASE,
                 channel: str = "can0", timeout: float = 1.0):
        self.transport = transport
        self.pids = pids
        self.tx_id = tx_id
        self.rx_id = rx_id
        self.channel = channel
        self._obd = ObdClient(transport, tx_id, rx_id, timeout)
        self.samples: List[dict] = []

    def poll_once(self) -> List[Frame]:
        frames: List[Frame] = []
        for pid in self.pids:
            probe_id = f"obd-01-{pid:02X}"
            data = self._obd.query_raw(pid, mode=0x01)
            now_mono, now_utc = time.monotonic(), time.time()
            # Log the request we emitted...
            frames.append(Frame(
                arb_id=self.tx_id, data=bytes([0x02, 0x01, pid, 0, 0, 0, 0, 0]),
                channel=self.channel, direction="tx", probe_id=probe_id,
                t_mono=now_mono, t_utc=now_utc,
            ))
            if data is None:
                continue
            # ...and the response it elicited.
            resp_bytes = bytes([len(data) + 2, 0x41, pid]) + data
            resp_bytes = resp_bytes[:8].ljust(8, b"\x00")
            frames.append(Frame(
                arb_id=self.rx_id, data=resp_bytes,
                channel=self.channel, direction="tx", probe_id=probe_id,
                t_mono=now_mono, t_utc=now_utc,
            ))
            sample = self._obd.sample_pid(pid)
            if sample is not None:
                sample["t_utc"] = now_utc
                self.samples.append(sample)
        return frames


# --------------------------------------------------------------------------- #
# Capture loop
# --------------------------------------------------------------------------- #
def capture(transport: Transport, writer: FrameWriter,
            duration: Optional[float] = None,
            poller: Optional[Poller] = None,
            poll_rate_hz: float = 5.0,
            recv_timeout: float = 0.2,
            stop: Optional[Callable[[], bool]] = None) -> int:
    """Sniff frames (and optionally poll) until ``duration`` / ``stop``.

    Returns the number of frames written during this call.
    """
    start = time.monotonic()
    poll_interval = 1.0 / poll_rate_hz if (poller and poll_rate_hz > 0) else None
    next_poll = start + (poll_interval or 0.0)
    written_before = writer.count

    while True:
        if stop is not None and stop():
            break
        if duration is not None and (time.monotonic() - start) >= duration:
            break

        for frame in transport.recv_frames(recv_timeout):
            writer.write(frame)

        if poll_interval is not None and time.monotonic() >= next_poll:
            for frame in poller.poll_once():
                writer.write(frame)
            next_poll += poll_interval

    writer.flush()
    return writer.count - written_before
