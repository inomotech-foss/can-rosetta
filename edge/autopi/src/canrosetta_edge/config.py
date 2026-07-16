"""Dataclass-based configuration for the edge component.

Sane defaults out of the box; overridable from a YAML file. All brute-force
knobs are deliberately conservative (throttled, bounded) per SAFETY.md.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional


@dataclass
class EdgeConfig:
    # --- bus / transport ---
    channel: str = "can0"
    bitrate: int = 500_000
    transport: str = "simulated"          # simulated | socketcan | elm
    elm_port: str = "/dev/ttyUSB0"
    elm_baudrate: int = 115200
    request_timeout_s: float = 1.0

    # --- output ---
    output_dir: str = "."                 # session directory root
    prefer_parquet: bool = True           # fall back to JSONL if pyarrow absent

    # --- polling (Stage 1b reference series) ---
    poll_rate_hz: float = 5.0             # OBD/UDS reference sampling rate

    # --- discovery brute-force (Stage 1a slow) ---
    brute_force_throttle_s: float = 0.05  # min gap between probes
    obd_pid_min: int = 0x00
    obd_pid_max: int = 0xFF
    uds_did_min: int = 0xF100             # bounded, resumable range
    uds_did_max: int = 0xF1FF
    plain_can_census_s: float = 10.0      # passive sniff window for census

    # --- logging ---
    log_duration_s: Optional[float] = None  # None == until stopped

    # --- edge onboard sensors (logged beside CAN, on the edge clock) ---
    sensors_enabled: bool = True
    sensor_source: str = "auto"           # auto | autopi | iio | simulated | none
    sensor_rate_hz: float = 50.0          # IMU sampling rate

    # --- control server (companion phone steers the AutoPi) ---
    control_host: str = "0.0.0.0"
    control_port: int = 8765
    control_token: str = ""               # pre-shared bearer token; "" disables auth (dev only)

    # --- power management ---
    prevent_sleep: bool = True            # hold the AutoPi awake while a job runs

    @classmethod
    def from_yaml(cls, path: str) -> "EdgeConfig":
        import yaml  # dependency: pyyaml

        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> "EdgeConfig":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"Unknown config keys: {sorted(unknown)}")
        return cls(**data)

    def to_dict(self) -> dict:
        return asdict(self)
