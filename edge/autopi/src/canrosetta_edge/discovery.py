"""Stage 1a -- Discovery.

Enumerate what the vehicle actually answers and emit a dict that validates
against ``schemas/discovery.schema.json``.

Two strategies (methodology.md):

* ``fast`` -- catalog scan: OBD supported-PID bitmasks + sample them; probe the
  standard UDS DID catalog against the standard ECU addresses.
* ``slow`` -- additionally brute-force OBD PIDs 0x00-0xFF, UDS DIDs over a
  bounded/throttled/resumable range, and run a passive plain-CAN census.

HARD CONSTRAINT: only the read services listed in SAFETY.md are ever issued.
Every OBD/UDS request flows through the guarded clients (:mod:`obd`, :mod:`uds`),
whose service-id guards raise on anything else.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Dict, List, Optional

from .config import EdgeConfig
from .obd import ObdClient, pid_hex
from .transport import (
    OBD_FUNCTIONAL_TX,
    OBD_RESP_BASE,
    Transport,
)
from .uds import DID_CATALOG, STANDARD_ECUS, UdsClient, did_hex

SCHEMA_VERSION = "1.0.0"


def discover(transport: Transport, mode: str = "fast",
             config: Optional[EdgeConfig] = None) -> dict:
    """Run discovery and return a schema-valid discovery dict."""
    if mode not in ("fast", "slow"):
        raise ValueError("mode must be 'fast' or 'slow'")
    config = config or EdgeConfig()

    result: dict = {"schema_version": SCHEMA_VERSION}
    result["obd"] = _discover_obd(transport, config, slow=(mode == "slow"))
    result["uds"] = _discover_uds(transport, config, slow=(mode == "slow"))
    if mode == "slow":
        result["plain_can"] = _plain_can_census(transport, config)
    return result


# --------------------------------------------------------------------------- #
# OBD
# --------------------------------------------------------------------------- #
def _discover_obd(transport: Transport, config: EdgeConfig, slow: bool) -> dict:
    client = ObdClient(transport, tx_id=OBD_FUNCTIONAL_TX, rx_id=OBD_RESP_BASE,
                       timeout=config.request_timeout_s)
    supported = client.enumerate_supported_pids()

    if slow:
        # Brute-force sweep of the full PID space; keep whatever responds.
        for pid in range(config.obd_pid_min, config.obd_pid_max + 1):
            if pid in supported:
                continue
            if client.query_raw(pid, mode=0x01) is not None:
                supported.append(pid)
            time.sleep(config.brute_force_throttle_s)
        supported = sorted(set(supported))

    samples: List[dict] = []
    for pid in supported:
        sample = client.sample_pid(pid)
        if sample is not None:
            sample["t_utc"] = time.time()
            samples.append(sample)

    return {
        "supported_pids": [pid_hex(p) for p in supported],
        "samples": samples,
    }


# --------------------------------------------------------------------------- #
# UDS
# --------------------------------------------------------------------------- #
def _discover_uds(transport: Transport, config: EdgeConfig, slow: bool) -> dict:
    ecus: List[dict] = []
    all_responding: List[int] = []

    catalog_dids = list(DID_CATALOG.keys())
    brute_dids = (
        range(config.uds_did_min, config.uds_did_max + 1) if slow else []
    )

    for tx_id, rx_id in STANDARD_ECUS:
        client = UdsClient(transport, tx_id=tx_id, rx_id=rx_id,
                           timeout=config.request_timeout_s)
        responding = client.probe_dids(catalog_dids)

        if slow:
            for did in brute_dids:
                if did in responding:
                    continue
                if client.read_data_by_identifier(did) is not None:
                    responding.append(did)
                time.sleep(config.brute_force_throttle_s)

        if responding:
            responding = sorted(set(responding))
            ecus.append({
                "tx_id": f"0x{tx_id:03X}",
                "rx_id": f"0x{rx_id:03X}",
                "dids": [did_hex(d) for d in responding],
            })
            all_responding.extend(responding)

    return {
        "responding_dids": [did_hex(d) for d in sorted(set(all_responding))],
        "ecus": ecus,
    }


# --------------------------------------------------------------------------- #
# Plain-CAN census (passive)
# --------------------------------------------------------------------------- #
def _plain_can_census(transport: Transport, config: EdgeConfig) -> dict:
    counts: Dict[int, int] = defaultdict(int)
    first_t: Dict[int, float] = {}
    last_t: Dict[int, float] = {}
    first_data: Dict[int, bytes] = {}
    changing: Dict[int, set] = defaultdict(set)

    deadline = time.monotonic() + config.plain_can_census_s
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        for frame in transport.recv_frames(min(0.5, max(0.01, remaining))):
            if frame.direction != "rx":
                continue
            aid = frame.arb_id
            counts[aid] += 1
            if aid not in first_t:
                first_t[aid] = frame.t_mono
                first_data[aid] = frame.data
            else:
                base = first_data[aid]
                for i in range(min(len(base), len(frame.data))):
                    if frame.data[i] != base[i]:
                        changing[aid].add(i)
            last_t[aid] = frame.t_mono

    arb_ids: List[dict] = []
    for aid in sorted(counts):
        n = counts[aid]
        span = last_t[aid] - first_t[aid]
        entry = {
            "arb_id": f"0x{aid:X}",
            "count": n,
            "changing_bytes": sorted(changing[aid]),
        }
        if n > 1 and span > 0:
            entry["period_ms_est"] = round(span / (n - 1) * 1000.0, 3)
        arb_ids.append(entry)

    return {"arb_ids": arb_ids}
