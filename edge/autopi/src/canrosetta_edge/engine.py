"""Edge engine: a small state machine the control API drives.

Wraps the existing discovery (Stage 1a) and logging (Stage 1b) code in a
single-job-at-a-time orchestrator with live status and an event stream, so the
companion phone can start an investigation, choose the mode, and start/stop
recording remotely (see docs/control-protocol.md).

The engine is transport-agnostic and does no networking itself — the control
server (:mod:`canrosetta_edge.control`) exposes it over HTTP/WebSocket. Jobs run
on a background thread; ``capture``'s ``stop`` callback is how logging is halted
and how live stats are emitted.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Callable, Optional

from .config import EdgeConfig
from .discovery import discover
from .logging_ import Poller, capture, make_frame_writer
from .obd import PIDS
from .session import (
    SessionLayout,
    build_manifest,
    default_device_id,
    frames_stream_entry,
    new_session_id,
    write_discovery,
    write_manifest,
)
from .session import SW_VERSION
from .power import make_wake_lock
from .sensors import SensorLogger, make_sensor_source
from .transport import (
    ElmTransport,
    NativeSocketCanTransport,
    SimulatedTransport,
    SocketCanTransport,
    Transport,
)

# engine states
IDLE = "idle"
DISCOVERING = "discovering"
LOGGING = "logging"
ERROR = "error"


def _python_can_available() -> bool:
    try:
        import can  # noqa: F401
        return True
    except Exception:
        return False


def make_transport(config: EdgeConfig, override: Optional[str] = None) -> Transport:
    """Construct the configured transport (shared by the CLI and the engine).

    For ``socketcan`` the backend is chosen by ``config.socketcan_backend``:
    ``python-can`` uses the library; ``native`` uses the stdlib-only
    :class:`NativeSocketCanTransport`; ``auto`` (default) prefers ``python-can``
    when importable and otherwise falls back to native -- so the edge runs on a
    stock AutoPi without ``pip``.
    """
    kind = override or config.transport
    if kind == "simulated":
        return SimulatedTransport(channel=config.channel)
    if kind == "socketcan":
        backend = config.socketcan_backend
        if backend == "python-can":
            return SocketCanTransport(channel=config.channel, bitrate=config.bitrate)
        if backend == "native":
            return NativeSocketCanTransport(channel=config.channel, bitrate=config.bitrate)
        # auto
        if _python_can_available():
            return SocketCanTransport(channel=config.channel, bitrate=config.bitrate)
        return NativeSocketCanTransport(channel=config.channel, bitrate=config.bitrate)
    if kind == "elm":
        return ElmTransport(port=config.elm_port, baudrate=config.elm_baudrate,
                            channel=config.channel)
    raise ValueError(f"unknown transport '{kind}'")


def _supported_poll_pids(discovery_result: dict) -> list[int]:
    pids = []
    for s in discovery_result.get("obd", {}).get("supported_pids", []):
        try:
            pid = int(s, 16)
        except (ValueError, TypeError):
            continue
        if pid in PIDS:
            pids.append(pid)
    return pids


class Engine:
    """Single-job orchestrator over discovery + logging."""

    def __init__(self, config: Optional[EdgeConfig] = None,
                 device_id: Optional[str] = None):
        self.config = config or EdgeConfig()
        self.device_id = device_id or default_device_id()

        self._lock = threading.RLock()
        self._state = IDLE
        self._stop = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._wake = make_wake_lock(self.config)

        self.session_id: Optional[str] = None
        self.layout: Optional[SessionLayout] = None
        self.mode: Optional[str] = None
        self.vehicle: Optional[dict] = None
        self.clock_source: str = "ntp"
        self.utc_offset_est_s: float = 0.0
        self.discovery_result: Optional[dict] = None
        self.error: Optional[str] = None

        # live logging stats
        self._log_started: Optional[float] = None
        self._frames: int = 0
        self._obd_samples: int = 0

        # event listeners: callables taking a dict
        self._listeners: set[Callable[[dict], None]] = set()

    # -- events ------------------------------------------------------------
    def subscribe(self, cb: Callable[[dict], None]) -> Callable[[], None]:
        with self._lock:
            self._listeners.add(cb)
        return lambda: self._listeners.discard(cb)

    def _emit(self, event: dict) -> None:
        event = {**event, "ts": time.time()}
        for cb in list(self._listeners):
            try:
                cb(event)
            except Exception:  # a slow/broken listener must not stall the engine
                pass

    def _set_state(self, state: str) -> None:
        with self._lock:
            self._state = state
        self._emit({"event": "state", "state": state})

    # -- status ------------------------------------------------------------
    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def status(self) -> dict:
        with self._lock:
            stats = {
                "elapsed_s": (time.monotonic() - self._log_started)
                if self._log_started else 0.0,
                "frames": self._frames,
                "obd_samples": self._obd_samples,
            }
            summary = None
            if self.discovery_result:
                summary = {
                    "obd_pids": len(self.discovery_result.get("obd", {})
                                    .get("supported_pids", [])),
                    "uds_dids": len(self.discovery_result.get("uds", {})
                                    .get("responding_dids", [])),
                    "plain_can_ids": len(self.discovery_result.get("plain_can", {})
                                         .get("arb_ids", [])),
                }
            return {
                "state": self._state,
                "session_id": self.session_id,
                "output_dir": self.layout.root if self.layout else None,
                "device": {"id": self.device_id, "sw_version": SW_VERSION},
                "mode": self.mode,
                "stats": stats,
                "discovery_summary": summary,
                "error": self.error,
            }

    # -- session -----------------------------------------------------------
    def start_session(self, session_id: Optional[str] = None,
                      vehicle: Optional[dict] = None,
                      edge_utc_offset_est_s: Optional[float] = None,
                      clock_source: Optional[str] = None) -> dict:
        with self._lock:
            if self._state in (DISCOVERING, LOGGING):
                raise Busy(self._state)
            self.session_id = session_id or new_session_id()
            self.layout = SessionLayout(
                os.path.join(self.config.output_dir, self.session_id), self.session_id
            ).ensure()
            self.vehicle = vehicle
            if edge_utc_offset_est_s is not None:
                self.utc_offset_est_s = float(edge_utc_offset_est_s)
            if clock_source:
                self.clock_source = clock_source
            self.discovery_result = None
            self.error = None
        self._emit({"event": "session", "session_id": self.session_id})
        return {
            "session_id": self.session_id,
            "output_dir": self.layout.root,
            "device": {"id": self.device_id, "sw_version": SW_VERSION},
        }

    def _ensure_session(self) -> None:
        if self.layout is None:
            self.start_session()

    # -- job control -------------------------------------------------------
    def _start_worker(self, target: Callable[[], None], initial_state: str) -> None:
        # Set the busy state synchronously under the lock *before* spawning the
        # worker, so a second start() racing right behind this one sees the busy
        # state and is rejected (the worker sets state again, harmlessly).
        with self._lock:
            if self._state in (DISCOVERING, LOGGING):
                raise Busy(self._state)
            self._state = initial_state
            self._stop.clear()
            self._worker = threading.Thread(target=self._guard(target), daemon=True)
            self._worker.start()
        self._emit({"event": "state", "state": initial_state})

    def _guard(self, target: Callable[[], None]) -> Callable[[], None]:
        def run() -> None:
            # hold the device awake for the whole job (no-op off real hardware)
            self._wake.acquire()
            try:
                target()
            except Exception as exc:  # surface failures as an error event/state
                self.error = f"{type(exc).__name__}: {exc}"
                self._emit({"event": "error", "message": self.error})
                self._set_state(ERROR)
            finally:
                self._wake.release()
                if self.state in (DISCOVERING, LOGGING):
                    self._set_state(IDLE)
        return run

    def start_discovery(self, mode: str) -> None:
        if mode not in ("fast", "slow"):
            raise ValueError("mode must be 'fast' or 'slow'")
        self._ensure_session()
        self.mode = mode
        self._start_worker(lambda: self._do_discovery(mode), DISCOVERING)

    def start_logging(self, duration_s: Optional[float] = None) -> None:
        self._ensure_session()
        self._start_worker(lambda: self._do_logging(duration_s), LOGGING)

    def start_run(self, mode: str, duration_s: Optional[float] = None) -> None:
        if mode not in ("fast", "slow"):
            raise ValueError("mode must be 'fast' or 'slow'")
        self._ensure_session()
        self.mode = mode

        def job() -> None:
            self._do_discovery(mode)
            if not self._stop.is_set():
                self._do_logging(duration_s)

        self._start_worker(job, DISCOVERING)

    def stop(self) -> dict:
        self._stop.set()
        worker = self._worker
        if worker is not None:
            worker.join(timeout=15.0)
        self._set_state(IDLE)
        return {"state": IDLE, "frames": self._frames}

    def wait(self, timeout: Optional[float] = None) -> None:
        """Block until the current job finishes (for synchronous/CLI use)."""
        worker = self._worker
        if worker is not None:
            worker.join(timeout=timeout)

    # -- jobs --------------------------------------------------------------
    def _do_discovery(self, mode: str) -> None:
        self._set_state(DISCOVERING)
        self._emit({"event": "discovery", "phase": "start", "mode": mode})
        with make_transport(self.config) as transport:
            result = discover(transport, mode=mode, config=self.config)
        self.discovery_result = result
        assert self.layout is not None
        write_discovery(self.layout.discovery_path, result)
        self._write_manifest(include_frames=None)
        summary = self.status()["discovery_summary"]
        self._emit({"event": "discovery_done", "summary": summary})

    def _do_logging(self, duration_s: Optional[float]) -> None:
        assert self.layout is not None
        self._set_state(LOGGING)
        self._log_started = time.monotonic()
        self._frames = 0
        self._obd_samples = 0

        # log the AutoPi's own IMU/GPS beside the CAN bus, on the edge clock
        sensor_logger: Optional[SensorLogger] = None
        if self.config.sensors_enabled:
            sensor_logger = SensorLogger(
                make_sensor_source(self.config),
                self.layout.edge_motion_path, self.layout.edge_location_path,
                rate_hz=self.config.sensor_rate_hz,
            )
            sensor_logger.start()

        with make_transport(self.config) as transport:
            writer = make_frame_writer(self.layout.frames_base, self.config.prefer_parquet)
            poller = None
            if self.discovery_result:
                pids = _supported_poll_pids(self.discovery_result)
                if pids:
                    poller = Poller(transport, pids, channel=self.config.channel,
                                    timeout=self.config.request_timeout_s)

            last_tick = [0.0]

            def stop_or_tick() -> bool:
                # emit live stats roughly every second, and report the stop flag
                now = time.monotonic()
                if now - last_tick[0] >= 1.0:
                    last_tick[0] = now
                    self._frames = writer.count
                    self._obd_samples = len(poller.samples) if poller else 0
                    self._emit({
                        "event": "stats", "frames": self._frames,
                        "obd_samples": self._obd_samples,
                        "elapsed_s": now - (self._log_started or now),
                    })
                return self._stop.is_set()

            try:
                capture(transport, writer,
                        duration=duration_s or self.config.log_duration_s,
                        poller=poller, poll_rate_hz=self.config.poll_rate_hz,
                        stop=stop_or_tick)
            finally:
                writer.close()
                if sensor_logger is not None:
                    sensor_logger.stop()
                self._frames = writer.count
                if poller and poller.samples:
                    self._fold_samples(poller.samples)
                self._obd_samples = len(poller.samples) if poller else 0
                self._write_manifest(include_frames=writer)

        self._emit({"event": "log_done", "frames": self._frames})

    # -- persistence helpers ----------------------------------------------
    def _fold_samples(self, samples: list) -> None:
        assert self.layout is not None
        result = self.discovery_result or {"schema_version": "1.0.0"}
        obd = result.setdefault("obd", {})
        obd.setdefault("samples", []).extend(samples)
        self.discovery_result = result
        write_discovery(self.layout.discovery_path, result)

    def _write_manifest(self, include_frames) -> None:
        assert self.layout is not None and self.session_id is not None
        streams: list[dict] = []
        if include_frames is not None:
            rel = os.path.relpath(include_frames.path, self.layout.root)
            streams.append(frames_stream_entry(rel, include_frames))
        if os.path.exists(self.layout.discovery_path):
            streams.append({"path": "can/discovery.json", "kind": "discovery"})
        if os.path.exists(self.layout.edge_motion_path):
            streams.append({"path": "edge/motion.jsonl", "kind": "motion"})
        if os.path.exists(self.layout.edge_location_path):
            streams.append({"path": "edge/location.jsonl", "kind": "location"})
        manifest = build_manifest(
            self.session_id, device_id=self.device_id,
            clock_source=self.clock_source,
            utc_offset_est_s=self.utc_offset_est_s,
            vehicle=self.vehicle, streams=streams,
        )
        write_manifest(self.layout.manifest_path, manifest)

    def read_discovery(self) -> Optional[dict]:
        if self.layout and os.path.exists(self.layout.discovery_path):
            with open(self.layout.discovery_path) as fh:
                return json.load(fh)
        return self.discovery_result


class Busy(RuntimeError):
    """Raised when a job is requested while one is already running."""

    def __init__(self, state: str):
        super().__init__(f"engine busy: {state}")
        self.state = state
