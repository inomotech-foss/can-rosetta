"""Speaker chirps: the audible "logger is ready" / "phone is connected" cues.

The AutoPi has a real onboard speaker (an I2S DAC — hifiberry-dac — behind a
small amplifier), and AutoPi Core's ``audio.play`` command raises the amplifier
and plays a file through it. That gives the driver a zero-UI signal:

* **ready chirp** — the device wakes on ignition (the SPM triggers on the
  voltage rise) and systemd starts ``serve`` immediately, so the moment the
  control API starts accepting connections effectively *is* "ignition + logger
  ready". Two ascending tones say "you can pull out your phone now".
* **connected chirp** — a short higher blip when the first authenticated client
  request lands: confirmation the phone actually reached the device.

Mirrors :mod:`.power`'s philosophy: best-effort, never fatal, and a no-op on
non-AutoPi hosts (dev laptops stay silent unless opted in with
``CANROSETTA_CHIRP=1`` + ``aplay``). The WAVs are synthesized in pure stdlib
and cached under the temp dir, so there are no bundled assets to ship.
"""

from __future__ import annotations

import math
import os
import shutil
import struct
import subprocess
import tempfile
import threading
import wave

from .config import EdgeConfig
from .power import _looks_like_autopi

_RATE = 44100  # samples/s; 16-bit mono is plenty for a short sine chirp

# name -> sequence of (frequency_hz, duration_s). Tones stay in 1-4 kHz — the
# sweet spot of the small onboard speaker — and each pair ascends so it reads
# as an "OK" rather than an alarm. The connected blip is shorter and higher so
# the two cues are distinguishable without looking at anything.
_CHIRPS = {
    "ready": ((1320.0, 0.18), (1760.0, 0.18)),      # E6 -> A6: logger is ready
    "connected": ((1760.0, 0.09), (2093.0, 0.09)),  # A6 -> C7 blip: phone connected
}


def _tone(freq_hz: float, duration_s: float, volume: float = 0.7,
          fade_s: float = 0.010) -> list:
    """One sine tone as 16-bit samples, raised-cosine faded at both edges.

    The ~10 ms fades matter: a hard-edged sine clicks audibly on a small
    speaker, which reads as a fault rather than a cue.
    """
    n = int(_RATE * duration_s)
    fade_n = min(int(_RATE * fade_s), n // 2)
    samples = []
    for i in range(n):
        amp = volume
        if fade_n and i < fade_n:
            amp *= 0.5 - 0.5 * math.cos(math.pi * i / fade_n)
        elif fade_n and i >= n - fade_n:
            amp *= 0.5 - 0.5 * math.cos(math.pi * (n - 1 - i) / fade_n)
        samples.append(int(32767 * amp * math.sin(2.0 * math.pi * freq_hz * i / _RATE)))
    return samples


def chirp_path(name: str) -> str:
    """Path of the cached chirp WAV, synthesizing it first when missing.

    A stable filename under the temp dir: generated once per boot (the
    AutoPi's tmpfs is cleared on reboot) and reused across restarts of the
    serve process.
    """
    path = os.path.join(tempfile.gettempdir(), f"canrosetta-chirp-{name}.wav")
    if not os.path.exists(path):
        samples: list = []
        for freq_hz, duration_s in _CHIRPS[name]:
            samples += _tone(freq_hz, duration_s)
        with wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(_RATE)
            wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return path


def _player() -> list | None:
    """Pick the playback command, or None to stay silent.

    On an AutoPi, AutoPi Core's ``audio.play`` raises the speaker amplifier
    before playing (a bare aplay would stay inaudible). Elsewhere, aplay is
    used only when the developer opts in with CANROSETTA_CHIRP=1 — test runs
    and dev laptops stay silent by default.
    """
    if _looks_like_autopi():
        salt = shutil.which("salt-call")
        if salt:
            return [salt, "--local", "audio.play"]
    if os.environ.get("CANROSETTA_CHIRP") == "1" and shutil.which("aplay"):
        return ["aplay", "-q"]
    return None


def _play(cmd: list, name: str) -> None:
    """Synthesize (if needed) and play one chirp; best-effort like power.py."""
    try:
        subprocess.run([*cmd, chirp_path(name)], timeout=15, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:  # noqa: BLE001
        print(f"[audio] chirp failed (best-effort): {exc}")


def _chirp(config: EdgeConfig, name: str) -> None:
    """Fire a chirp without blocking the caller (playback shells out — slow)."""
    if not getattr(config, "chirp", True):
        return
    cmd = _player()
    if cmd is None:
        return  # nothing to play on this host — skip the thread entirely
    threading.Thread(target=_play, args=(cmd, name), daemon=True).start()


def chirp_ready(config: EdgeConfig) -> None:
    """Ascending double-tone: control API is up == ignition-ready. Returns at once."""
    _chirp(config, "ready")


def chirp_connected(config: EdgeConfig) -> None:
    """Short high blip: the first authenticated client reached us. Returns at once."""
    _chirp(config, "connected")
