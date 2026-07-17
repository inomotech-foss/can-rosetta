"""Generate a synthetic sample session.

This is the backbone of the offline demo and the end-to-end test: a fully
self-contained drive with a known ground truth, so we can assert that the
pipeline recovers it. It writes a real session directory in the shared format
(see docs/data-format.md), including a JSONL CAN log (no pyarrow needed).

Ground truth baked in:
- a plain-CAN speed broadcast on 0x3C0, bytes 1-2 big-endian, scale 0.01 km/h,
  plus a rolling message counter and a checksum byte (which the extractor must
  reject) and constant padding;
- a plain-CAN RPM broadcast on 0x1F0, bytes 0-1 big-endian;
- OBD samples (vehicle_speed, rpm, coolant_temp) in discovery.json;
- GPS + IMU on the companion clock.

A deliberate clock offset separates the edge and companion clocks so alignment
has something real to recover.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

SCHEMA = "1.0.0"
SPEED_ID = 0x3C0
RPM_ID = 0x1F0
NOISE_ID = 0x2A0
BODY_ID = 0x3B0  # body module: turn-signal telltale (byte0 MSB) + gear (byte1)
BATT_ID = 0x4A0  # EV battery module: pack voltage, signed pack current, SoC


def _turn_signal(t: float) -> int:
    """1.5 Hz blink during two active windows; off otherwise (dashboard-visible)."""
    active = (20.0 <= t <= 35.0) or (80.0 <= t <= 95.0)
    return 1 if (active and math.sin(2 * math.pi * 1.5 * t) > 0) else 0


def _speed_profile(t: np.ndarray) -> np.ndarray:
    """A plausible km/h speed curve: accelerate, cruise, brake, stop, go."""
    s = (
        28 * (1 - np.exp(-t / 8.0))  # ramp up
        + 12 * np.sin(2 * math.pi * t / 40.0)  # cruise wobble
    )
    s = np.clip(s, 0, None)
    s[t > 70] *= np.clip(1 - (t[t > 70] - 70) / 20.0, 0, 1)  # brake to stop
    s[(t > 90) & (t < 95)] = 0
    s[t >= 95] += 20 * (1 - np.exp(-(t[t >= 95] - 95) / 5.0))  # go again
    return np.clip(s, 0, None)


def _checksum(b: bytes) -> int:
    c = 0
    for x in b:
        c = (c + x) & 0xFF
    return c ^ 0xA5


def generate_ev(out_dir: str | Path, **kw) -> Path:
    """Write a synthetic *electric*-vehicle session (adds a battery module)."""
    return generate(out_dir, ev=True, **kw)


def generate(
    out_dir: str | Path,
    *,
    duration_s: float = 120.0,
    edge_clock_offset_s: float = 0.7,
    seed: int = 7,
    ev: bool = False,
) -> Path:
    """Write a synthetic session and return its path.

    With ``ev=True`` a battery module (0x4A0) is added: pack voltage, signed pack
    current (positive under acceleration, **negative under regen**), and SoC —
    plus EV OBD samples — so EV signal identification can be exercised.
    """
    rng = np.random.default_rng(seed)
    out = Path(out_dir)
    (out / "can").mkdir(parents=True, exist_ok=True)
    (out / "phone").mkdir(parents=True, exist_ok=True)

    t0_true = 1_752_624_000.0  # arbitrary session start on the "true" clock

    # ---- ground-truth physical signals on a fine grid --------------------
    tt = np.arange(0, duration_s, 0.01)
    speed_kmh = _speed_profile(tt)
    speed_ms = speed_kmh / 3.6
    accel_ms2 = np.gradient(speed_ms, tt)
    # Engine RPM through a simple gearbox: within each gear rpm rises with speed,
    # then drops on the upshift. This decouples rpm from absolute speed (as in a
    # real drive) so the two signals are distinguishable, not collinear.
    gear = np.clip((speed_kmh // 12).astype(int), 0, 5)
    turn = np.array([_turn_signal(float(x)) for x in tt])
    frac_in_gear = (speed_kmh - gear * 12) / 12.0

    # EV battery: current tracks signed longitudinal accel (drive +, regen -);
    # voltage sags under discharge; SoC coulomb-counts down from 80%.
    accel_g = accel_ms2 / 9.81
    # high gain + low base so deceleration drives current clearly NEGATIVE (regen):
    # this is what makes the *signed* interpretation win over unsigned.
    batt_current_a = 400.0 * accel_g + 5.0 + rng.normal(0, 1.5, tt.shape)  # signed amps
    batt_voltage_v = 360.0 - 0.05 * batt_current_a + rng.normal(0, 0.2, tt.shape)
    batt_soc = 80.0 - np.cumsum(batt_current_a * 0.01) / 3600.0 * 100.0 / 60.0
    base_rpm = 1000 + frac_in_gear * 2500  # 1000 idle .. ~3500 redline-ish
    rpm_can = base_rpm + rng.normal(0, 15, tt.shape)  # noise seen by the CAN frame

    # ---- CAN log (edge clock = true + offset) ----------------------------
    frames: list[dict] = []
    counter = 0

    def speed_frame(i: int) -> bytes:
        nonlocal counter
        raw = int(round(speed_kmh[i] / 0.01)) & 0xFFFF  # scale 0.01 km/h
        b = bytearray(8)
        b[0] = counter & 0xFF  # rolling message counter -> must be rejected
        b[1] = (raw >> 8) & 0xFF  # speed MSB (big-endian, bytes 1-2)
        b[2] = raw & 0xFF  # speed LSB
        b[3] = 0x00  # constant padding -> must be rejected
        b[4] = 0x55  # constant padding
        b[7] = _checksum(bytes(b[:7]))  # checksum byte -> must be rejected
        counter = (counter + 1) & 0xFF
        return bytes(b)

    def rpm_frame(i: int) -> bytes:
        raw = int(round(rpm_can[i])) & 0xFFFF
        b = bytearray(8)
        b[0] = (raw >> 8) & 0xFF  # rpm MSB (big-endian, bytes 0-1)
        b[1] = raw & 0xFF
        b[2] = 0x00
        return bytes(b)

    def body_frame(i: int) -> bytes:
        b = bytearray(8)
        b[0] = 0x80 if turn[i] else 0x00  # turn-signal telltale in byte0 MSB (bit index 0)
        b[1] = int(gear[i]) & 0xFF  # gear enum in byte1
        b[3] = 0x55  # constant padding
        return bytes(b)

    def batt_frame(i: int) -> bytes:
        b = bytearray(8)
        v = int(round(batt_voltage_v[i] * 10)) & 0xFFFF  # 0.1 V/bit, bytes 0-1 BE
        c = int(round(batt_current_a[i] * 10)) & 0xFFFF  # signed 0.1 A/bit, bytes 2-3 BE
        soc = int(round(batt_soc[i] * 10)) & 0xFFFF  # 0.1 %/bit, bytes 4-5 BE
        b[0], b[1] = (v >> 8) & 0xFF, v & 0xFF
        b[2], b[3] = (c >> 8) & 0xFF, c & 0xFF
        b[4], b[5] = (soc >> 8) & 0xFF, soc & 0xFF
        return bytes(b)

    # 0x3C0 @ 50 Hz, 0x1F0 @ 20 Hz, plus a noise frame @ 10 Hz
    for k in range(int(duration_s * 50)):
        i = min(int(k / 50 / 0.01), len(tt) - 1)
        mono = k / 50.0
        frames.append(_frame(mono, t0_true + edge_clock_offset_s, SPEED_ID, speed_frame(i)))
    for k in range(int(duration_s * 20)):
        i = min(int(k / 20 / 0.01), len(tt) - 1)
        mono = k / 20.0
        frames.append(_frame(mono, t0_true + edge_clock_offset_s, RPM_ID, rpm_frame(i)))
    for k in range(int(duration_s * 20)):  # body module @ 20 Hz
        i = min(int(k / 20 / 0.01), len(tt) - 1)
        mono = k / 20.0
        frames.append(_frame(mono, t0_true + edge_clock_offset_s, BODY_ID, body_frame(i)))
    if ev:
        for k in range(int(duration_s * 20)):  # battery module @ 20 Hz
            i = min(int(k / 20 / 0.01), len(tt) - 1)
            mono = k / 20.0
            frames.append(_frame(mono, t0_true + edge_clock_offset_s, BATT_ID, batt_frame(i)))
    for k in range(int(duration_s * 10)):
        mono = k / 10.0
        noise = bytes(int(x) for x in rng.integers(0, 256, 8))
        frames.append(_frame(mono, t0_true + edge_clock_offset_s, NOISE_ID, noise))

    frames.sort(key=lambda f: f["t_mono"])
    with (out / "can" / "frames.jsonl").open("w", encoding="utf-8") as fh:
        for f in frames:
            fh.write(json.dumps(f) + "\n")

    # ---- discovery.json (OBD samples on edge clock) ----------------------
    obd_samples = []
    for k in range(int(duration_s * 2)):  # 2 Hz OBD polling
        i = min(int(k / 2 / 0.01), len(tt) - 1)
        t_utc = t0_true + edge_clock_offset_s + k / 2.0
        obd_rpm = float(base_rpm[i]) + rng.normal(0, 15)  # OBD sees independent noise
        obd_samples.append(_obd(t_utc, 0x0D, "vehicle_speed", round(float(speed_kmh[i])), "km/h"))
        obd_samples.append(_obd(t_utc, 0x0C, "engine_rpm", round(obd_rpm), "rpm"))
        obd_samples.append(_obd(t_utc, 0x05, "coolant_temp", 40 + round(k / 20), "degC"))
        if ev:  # EV state-of-charge is readable via a standard-ish OBD PID
            obd_samples.append(_obd(t_utc, 0x5B, "hybrid_battery_remaining",
                                    round(float(batt_soc[i]), 1), "%"))
    supported = ["0x05", "0x0C", "0x0D"] + (["0x5B"] if ev else [])
    discovery = {
        "schema_version": SCHEMA,
        "obd": {"supported_pids": supported, "samples": obd_samples},
        "uds": {"responding_dids": ["0xF190"], "ecus": [{"tx_id": "0x7E0", "rx_id": "0x7E8"}]},
        "plain_can": {
            "arb_ids": [
                {"arb_id": hex(SPEED_ID), "count": len(frames), "period_ms_est": 20},
                {"arb_id": hex(RPM_ID), "count": 0, "period_ms_est": 50},
            ]
        },
    }
    (out / "can" / "discovery.json").write_text(json.dumps(discovery, indent=2))

    # ---- phone GPS + IMU (companion clock = true) ------------------------
    with (out / "phone" / "location.jsonl").open("w", encoding="utf-8") as fh:
        lat, lon = 48.137, 11.575
        for k in range(int(duration_s * 5)):  # 5 Hz GPS
            i = min(int(k / 5 / 0.01), len(tt) - 1)
            t_utc = t0_true + k / 5.0
            # crude dead-reckon north for a moving lat
            lat += speed_ms[i] / 111_320.0 / 5.0
            rec = {
                "t_utc": t_utc,
                "lat": round(lat, 7),
                "lon": round(lon, 7),
                "alt": 519.0,
                "speed": round(float(speed_ms[i]) + rng.normal(0, 0.15), 3),
                "course": 0.0,
                "h_acc": 4.0,
                "v_acc": 6.0,
            }
            fh.write(json.dumps(rec) + "\n")

    with (out / "phone" / "motion.jsonl").open("w", encoding="utf-8") as fh:
        for k in range(int(duration_s * 100)):  # 100 Hz IMU
            i = min(int(k / 100 / 0.01), len(tt) - 1)
            t_utc = t0_true + k / 100.0
            ax = float(accel_ms2[i]) / 9.81 + rng.normal(0, 0.01)  # longitudinal, g
            rec = {
                "t_utc": t_utc,
                "acc": [round(ax, 5), round(rng.normal(0, 0.01), 5), round(rng.normal(0, 0.01), 5)],
                "gravity": [0.0, 0.0, -1.0],
                "rot": [0.0, 0.0, round(rng.normal(0, 0.005), 5)],
                "att": [0.0, 0.0, 0.0],
                "mag": None,
            }
            fh.write(json.dumps(rec) + "\n")

    # ---- edge onboard sensors (edge clock = true + offset) ---------------
    # The AutoPi's own IMU/GPS, on the SAME clock as the CAN frames.
    (out / "edge").mkdir(parents=True, exist_ok=True)
    with (out / "edge" / "motion.jsonl").open("w", encoding="utf-8") as fh:
        for k in range(int(duration_s * 100)):  # 100 Hz IMU
            i = min(int(k / 100 / 0.01), len(tt) - 1)
            t_utc = t0_true + edge_clock_offset_s + k / 100.0
            ax = float(accel_ms2[i]) / 9.81 + rng.normal(0, 0.01)
            fh.write(json.dumps({
                "t_utc": round(t_utc, 4),
                "acc": [round(ax, 5), round(rng.normal(0, 0.01), 5),
                        round(rng.normal(0, 0.01), 5)],
                "rot": [0.0, 0.0, round(rng.normal(0, 0.005), 5)],
            }) + "\n")
    with (out / "edge" / "location.jsonl").open("w", encoding="utf-8") as fh:
        for k in range(int(duration_s * 5)):  # 5 Hz GPS
            i = min(int(k / 5 / 0.01), len(tt) - 1)
            t_utc = t0_true + edge_clock_offset_s + k / 5.0
            fh.write(json.dumps({
                "t_utc": round(t_utc, 4), "lat": 48.137, "lon": 11.575, "alt": 519.0,
                "speed": round(float(speed_ms[i]) + rng.normal(0, 0.15), 3),
                "course": -1.0, "h_acc": 4.0, "v_acc": 6.0,
            }) + "\n")

    # ---- dashboard-video labels (companion clock = true) -----------------
    # As if produced by canrosetta.perception from the filmed dashboard.
    (out / "labels").mkdir(parents=True, exist_ok=True)
    with (out / "labels" / "telltales.jsonl").open("w", encoding="utf-8") as fh:
        for k in range(int(duration_s * 10)):  # 10 Hz
            i = min(int(k / 10 / 0.01), len(tt) - 1)
            fh.write(json.dumps({"t_utc": round(t0_true + k / 10.0, 4),
                                 "name": "turn_signal", "state": int(turn[i])}) + "\n")
    with (out / "labels" / "gear.jsonl").open("w", encoding="utf-8") as fh:
        for k in range(int(duration_s * 5)):  # 5 Hz
            i = min(int(k / 5 / 0.01), len(tt) - 1)
            fh.write(json.dumps({"t_utc": round(t0_true + k / 5.0, 4),
                                 "gear": int(gear[i])}) + "\n")

    # ---- manifest --------------------------------------------------------
    manifest = {
        "schema_version": SCHEMA,
        "session_id": "synthetic-demo",
        "created_utc": t0_true,
        "vehicle": {"make": "Synthetic", "model": "Testcar", "year": 2020},
        "devices": [
            {
                "role": "edge",
                "kind": "autopi",
                "id": "sim-edge",
                "sw_version": "canrosetta-synth",
                "clock": {"source": "ntp", "utc_offset_est_s": 0.0, "err_est_s": 1.0},
            },
            {
                "role": "companion",
                "kind": "ios",
                "id": "sim-phone",
                "sw_version": "canrosetta-synth",
                "clock": {"source": "gps", "utc_offset_est_s": 0.0, "err_est_s": 0.05},
            },
        ],
        "streams": [
            {"path": "can/frames.jsonl", "kind": "can_frames", "rows": len(frames)},
            {"path": "can/discovery.json", "kind": "discovery"},
            {"path": "phone/motion.jsonl", "kind": "motion"},
            {"path": "phone/location.jsonl", "kind": "location"},
            {"path": "edge/motion.jsonl", "kind": "motion"},
            {"path": "edge/location.jsonl", "kind": "location"},
        ],
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))

    # Ground truth is a test-only sidecar, kept OUT of the manifest so the
    # manifest stays valid against the (strict) shared schema. Real sessions
    # never contain this file.
    ground_truth = {
        "note": "for tests/demos only; real sessions never contain this",
        "speed": {"arb_id": hex(SPEED_ID), "bytes": [1, 2], "endian": "big",
                  "scale": 0.01, "unit": "km/h"},
        "rpm": {"arb_id": hex(RPM_ID), "bytes": [0, 1], "endian": "big", "scale": 1.0},
        "turn_signal": {"arb_id": hex(BODY_ID), "byte": 0, "bit": "MSB"},
        "gear": {"arb_id": hex(BODY_ID), "byte": 1},
        "edge_clock_offset_s": edge_clock_offset_s,
    }
    if ev:
        ground_truth["ev"] = {
            "hv_battery_voltage": {"arb_id": hex(BATT_ID), "bytes": [0, 1], "scale": 0.1},
            "hv_battery_current": {"arb_id": hex(BATT_ID), "bytes": [2, 3], "signed": True,
                                   "scale": 0.1, "note": "negative under regen"},
            "hv_battery_soc": {"arb_id": hex(BATT_ID), "bytes": [4, 5], "scale": 0.1, "unit": "%"},
        }
    (out / "ground_truth.json").write_text(json.dumps(ground_truth, indent=2))
    return out


def _frame(mono: float, t_utc_base: float, arb_id: int, data: bytes) -> dict:
    return {
        "t_mono": round(mono, 4),
        "t_utc": round(t_utc_base + mono, 4),
        "channel": "can0",
        "arb_id": arb_id,
        "is_extended": False,
        "dlc": len(data),
        "data": data.hex(),
        "direction": "rx",
        "probe_id": None,
    }


def _obd(t_utc: float, pid: int, name: str, value: float, unit: str) -> dict:
    return {
        "mode": 1,
        "pid": hex(pid),
        "name": name,
        "t_utc": round(t_utc, 4),
        "raw": "",
        "value": value,
        "unit": unit,
    }
