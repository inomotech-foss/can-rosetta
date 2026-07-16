"""Keep the AutoPi awake while a job is running.

AutoPi Core puts the device to sleep on inactivity / when the vehicle is off, to
protect the battery. That is fatal to a long recording, so the engine holds a
*wake lock* for the whole time it is discovering or logging and releases it when
it returns to idle.

The AutoPi implementation refreshes the platform's sleep timer on a heartbeat
(best-effort; it shells out to the AutoPi power manager and tolerates failure).
On non-AutoPi hosts a no-op lock is used. Everything is behind a small interface
so it's swappable and testable.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from typing import Optional

from .config import EdgeConfig


class WakeLock:
    """Interface: hold the device awake between acquire() and release()."""

    def acquire(self) -> None:  # pragma: no cover - trivial
        ...

    def release(self) -> None:  # pragma: no cover - trivial
        ...

    def __enter__(self) -> "WakeLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()


class NoopWakeLock(WakeLock):
    """Does nothing — for simulated transports and non-AutoPi dev machines."""

    def acquire(self) -> None:
        pass

    def release(self) -> None:
        pass


class AutoPiWakeLock(WakeLock):
    """Refresh the AutoPi sleep timer on a heartbeat so it never sleeps.

    AutoPi Core exposes a ``power.sleep_timer`` you can (re)arm; we keep pushing
    it out beyond ``refresh_s`` so the device stays awake as long as we hold the
    lock. Commands run via ``salt-call`` and are best-effort — a failure is
    logged, never fatal to the recording.
    """

    def __init__(self, refresh_s: float = 60.0, hold_s: float = 300.0,
                 runner: Optional[list[str]] = None):
        self.refresh_s = refresh_s
        self.hold_s = hold_s
        self._runner = runner or self._default_runner()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @staticmethod
    def _default_runner() -> Optional[list[str]]:
        salt = shutil.which("salt-call")
        return [salt, "--local", "power.sleep_timer"] if salt else None

    def available(self) -> bool:
        return self._runner is not None

    def _arm(self) -> None:
        if not self._runner:
            return
        # push the sleep timer well past our refresh interval
        cmd = [*self._runner, "add", "name=can-rosetta", f"delay={int(self.hold_s)}"]
        try:
            subprocess.run(cmd, timeout=10, check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:  # noqa: BLE001
            print(f"[power] wake-lock heartbeat failed (best-effort): {exc}")

    def _clear(self) -> None:
        if not self._runner:
            return
        try:
            subprocess.run([*self._runner, "clear", "name=can-rosetta"], timeout=10,
                           check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:  # noqa: BLE001
            pass

    def acquire(self) -> None:
        if not self.available():
            print("[power] salt-call not found; cannot hold AutoPi awake "
                  "(is this really an AutoPi?)")
            return
        self._stop.clear()

        def beat() -> None:
            while not self._stop.is_set():
                self._arm()
                self._stop.wait(self.refresh_s)

        self._thread = threading.Thread(target=beat, daemon=True)
        self._thread.start()

    def release(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        self._clear()


def _looks_like_autopi() -> bool:
    return (
        os.path.exists("/opt/autopi")
        or os.environ.get("CANROSETTA_FORCE_AUTOPI") == "1"
        or shutil.which("salt-call") is not None
    )


def make_wake_lock(config: EdgeConfig) -> WakeLock:
    """Pick a wake lock: AutoPi on real hardware, no-op elsewhere."""
    if not getattr(config, "prevent_sleep", True):
        return NoopWakeLock()
    if config.transport == "simulated":
        return NoopWakeLock()
    if _looks_like_autopi():
        return AutoPiWakeLock()
    return NoopWakeLock()
