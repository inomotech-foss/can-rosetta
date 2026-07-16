"""Log the AutoPi's own onboard sensors (IMU, GPS) beside the CAN bus.

The AutoPi has an accelerometer/gyro (and often GPS). Logging them here is
especially valuable because these samples are on the **same clock as the CAN
frames** — no cross-device alignment is needed to correlate an edge-IMU
acceleration reference against a candidate CAN signal. They complement (and are
more reliable than) the phone's sensors for identifying motion signals.

Records use the shared motion/location schemas (same as the phone), written to
``edge/motion.jsonl`` and ``edge/location.jsonl`` on the edge clock.

Sources are behind a small interface: a simulated one for tests/dev, an AutoPi
one (best-effort via the platform's sensor managers), and a generic Linux IIO
one. Hardware reads are lazy and failure-tolerant.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import threading
import time
from typing import Optional

from .config import EdgeConfig


class SensorSource:
    """Interface: sample the device's motion/location at the current instant."""

    def read_motion(self) -> Optional[dict]:
        return None

    def read_location(self) -> Optional[dict]:
        return None

    def available(self) -> bool:
        return False


class SimulatedSensorSource(SensorSource):
    """Synthesizes gentle motion (and a static location) for tests and demos."""

    def __init__(self) -> None:
        self._t0 = time.monotonic()

    def read_motion(self) -> dict:
        t = time.monotonic() - self._t0
        ax = 0.02 * math.sin(t)  # small longitudinal wiggle, g
        return {
            "acc": [round(ax, 5), 0.0, 0.0],
            "gravity": [0.0, 0.0, -1.0],
            "rot": [0.0, 0.0, round(0.01 * math.cos(t), 5)],
            "att": [0.0, 0.0, 0.0],
            "mag": None,
        }

    def read_location(self) -> dict:
        return {"lat": 48.137, "lon": 11.575, "alt": 519.0,
                "speed": -1.0, "course": -1.0, "h_acc": 5.0, "v_acc": 8.0}

    def available(self) -> bool:
        return True


class AutoPiSensorSource(SensorSource):
    """Read the AutoPi accelerometer/GPS via the platform managers (best-effort).

    Uses ``salt-call`` (``acc.query`` for the IMU, ``ec2x.gnss_location`` /
    ``gnss`` for GPS depending on the unit). Any failure yields ``None`` for that
    sample rather than raising, so a missing sensor never stops logging.
    """

    def __init__(self, runner: Optional[list[str]] = None) -> None:
        salt = shutil.which("salt-call")
        self._salt = runner or ([salt, "--local", "--out=json"] if salt else None)

    def available(self) -> bool:
        return self._salt is not None

    def _call(self, *args: str) -> Optional[dict]:
        if not self._salt:
            return None
        try:
            out = subprocess.run([*self._salt, *args], timeout=5, check=False,
                                 capture_output=True, text=True)
            if out.returncode != 0 or not out.stdout.strip():
                return None
            data = json.loads(out.stdout)
            return data.get("local", data) if isinstance(data, dict) else None
        except Exception:  # noqa: BLE001
            return None

    def read_motion(self) -> Optional[dict]:
        # AutoPi acc.query returns g values; gyro exposure varies by unit.
        acc = self._call("acc.query", "xyz")
        if not acc:
            return None
        try:
            g = [float(acc["x"]), float(acc["y"]), float(acc["z"])]
        except (KeyError, TypeError, ValueError):
            return None
        return {"acc": g, "rot": [0.0, 0.0, 0.0]}

    def read_location(self) -> Optional[dict]:
        loc = self._call("ec2x.gnss_location") or self._call("gnss.status")
        if not loc:
            return None
        try:
            return {"lat": float(loc["lat"]), "lon": float(loc["lon"]),
                    "alt": float(loc.get("alt", 0.0)),
                    "speed": float(loc.get("sog", -1.0)),
                    "course": float(loc.get("cog", -1.0)),
                    "h_acc": float(loc.get("hdop", 0.0)) * 5.0, "v_acc": 0.0}
        except (KeyError, TypeError, ValueError):
            return None


class IioSensorSource(SensorSource):
    """Generic Linux IIO accelerometer read from sysfs (best-effort)."""

    def __init__(self, base: str = "/sys/bus/iio/devices") -> None:
        self._accel_dev = self._find_accel(base)

    @staticmethod
    def _find_accel(base: str) -> Optional[str]:
        if not os.path.isdir(base):
            return None
        for name in sorted(os.listdir(base)):
            dev = os.path.join(base, name)
            if os.path.exists(os.path.join(dev, "in_accel_x_raw")):
                return dev
        return None

    def available(self) -> bool:
        return self._accel_dev is not None

    def _read(self, fname: str) -> Optional[float]:
        try:
            with open(os.path.join(self._accel_dev, fname)) as fh:  # type: ignore[arg-type]
                return float(fh.read().strip())
        except Exception:  # noqa: BLE001
            return None

    def read_motion(self) -> Optional[dict]:
        if not self._accel_dev:
            return None
        scale = self._read("in_accel_scale") or 1.0
        vals = [self._read(f"in_accel_{ax}_raw") for ax in ("x", "y", "z")]
        if any(v is None for v in vals):
            return None
        # raw*scale is m/s^2; convert to g for the shared schema
        g = [round(v * scale / 9.80665, 5) for v in vals]  # type: ignore[operator]
        return {"acc": g, "rot": [0.0, 0.0, 0.0]}


def make_sensor_source(config: EdgeConfig) -> SensorSource:
    src = config.sensor_source
    if src == "none" or not config.sensors_enabled:
        return SensorSource()  # inert
    if src == "simulated":
        return SimulatedSensorSource()
    if src == "autopi":
        return AutoPiSensorSource()
    if src == "iio":
        return IioSensorSource()
    # auto
    if config.transport == "simulated":
        return SimulatedSensorSource()
    autopi = AutoPiSensorSource()
    if autopi.available():
        return autopi
    iio = IioSensorSource()
    if iio.available():
        return iio
    return SensorSource()


class SensorLogger:
    """Background thread that samples the edge sensors into JSONL files."""

    def __init__(self, source: SensorSource, motion_path: str, location_path: str,
                 rate_hz: float = 50.0, location_rate_hz: float = 1.0):
        self.source = source
        self.motion_path = motion_path
        self.location_path = location_path
        self.rate_hz = rate_hz
        self.location_rate_hz = location_rate_hz
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.motion_count = 0
        self.location_count = 0

    def start(self) -> None:
        if not self.source.available():
            return
        os.makedirs(os.path.dirname(os.path.abspath(self.motion_path)), exist_ok=True)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        interval = 1.0 / self.rate_hz if self.rate_hz > 0 else 0.02
        loc_interval = 1.0 / self.location_rate_hz if self.location_rate_hz > 0 else None
        next_loc = time.monotonic() + (loc_interval or 1e9)
        with open(self.motion_path, "a", encoding="utf-8") as mf, \
                open(self.location_path, "a", encoding="utf-8") as lf:
            while not self._stop.is_set():
                loop_start = time.monotonic()
                m = self.source.read_motion()
                if m is not None:
                    m = {"t_utc": time.time(), **m}
                    mf.write(json.dumps(m) + "\n")
                    self.motion_count += 1
                    if self.motion_count % 25 == 0:
                        mf.flush()
                if loc_interval is not None and time.monotonic() >= next_loc:
                    loc = self.source.read_location()
                    if loc is not None:
                        loc = {"t_utc": time.time(), **loc}
                        lf.write(json.dumps(loc) + "\n")
                        self.location_count += 1
                        lf.flush()
                    next_loc += loc_interval
                slack = interval - (time.monotonic() - loop_start)
                if slack > 0:
                    self._stop.wait(slack)
            mf.flush()
            lf.flush()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    def has_data(self) -> bool:
        return self.motion_count > 0 or self.location_count > 0
