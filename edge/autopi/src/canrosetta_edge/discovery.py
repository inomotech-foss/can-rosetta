"""Stage 1a -- Discovery.

Enumerate what the vehicle actually answers and emit a dict that validates
against ``schemas/discovery.schema.json``.

Two strategies (methodology.md):

* ``fast`` -- catalog scan: OBD supported-PID bitmasks + sample them; probe the
  standard UDS DID catalog against the standard ECU addresses.
* ``slow`` -- additionally brute-force OBD PIDs 0x00-0xFF, UDS DIDs over a
  bounded/throttled/resumable range, and run a passive plain-CAN census.

Both 11-bit (ISO 15765-4) and 29-bit ("normal fixed", used by Mercedes-Benz and
others) diagnostic addressing are tried, controlled by ``config.diag_addressing``.
When the transport can broadcast a functional request and collect *all* ECU
responses at once (:meth:`request_all`), discovery enumerates every responding
ECU; otherwise it falls back to the standard single-responder path.

HARD CONSTRAINT: only the read services listed in SAFETY.md are ever issued.
Every OBD/UDS request flows through the guarded clients (:mod:`obd`, :mod:`uds`),
whose service-id guards raise on anything else.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional

from .config import EdgeConfig
from .obd import PIDS, SUPPORT_QUERY_PIDS, ObdClient, parse_supported_pids, pid_hex
from .transport import (
    OBD_FUNCTIONAL_TX,
    OBD_FUNCTIONAL_TX_29,
    OBD_PHYSICAL_TX_BASE,
    OBD_RESP_BASE,
    Transport,
    phys_req_29,
    phys_resp_29,
    req_id_for_response,
)
from .uds import (
    COMMON_ECU_ADDRS_29,
    DID_CATALOG,
    STANDARD_ECUS,
    UdsClient,
    decode_dtc,
    did_hex,
)

SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True)
class Addressing:
    """One diagnostic addressing scheme to try."""

    name: str            # "11bit" | "29bit"
    functional_tx: int   # broadcast request id
    is_extended: bool
    response_ids: frozenset  # legal response arbitration ids


def _addressing_profiles(config: EdgeConfig) -> List[Addressing]:
    want = (config.diag_addressing or "both").lower()
    profiles: List[Addressing] = []
    if want in ("11bit", "both"):
        profiles.append(Addressing(
            "11bit", OBD_FUNCTIONAL_TX, False,
            frozenset(range(OBD_RESP_BASE, OBD_RESP_BASE + 8)),
        ))
    if want in ("29bit", "both"):
        profiles.append(Addressing(
            "29bit", OBD_FUNCTIONAL_TX_29, True,
            frozenset(phys_resp_29(e) for e in range(0x100)),
        ))
    return profiles


def _physical_pairs(prof: Addressing, deep: bool):
    """(tx_id, rx_id) physical ECU addresses to probe for one addressing scheme.

    Physical addressing matters because some vehicles (e.g. Mercedes-Benz) answer
    diagnostics *only* when addressed physically, ignoring the functional
    broadcast entirely.
    """
    if prof.name == "11bit":
        return [(OBD_PHYSICAL_TX_BASE + i, OBD_RESP_BASE + i) for i in range(8)]
    ecus = range(0x100) if deep else COMMON_ECU_ADDRS_29
    return [(phys_req_29(e), phys_resp_29(e)) for e in ecus]


def discover(transport: Transport, mode: str = "fast",
             config: Optional[EdgeConfig] = None,
             bus: Optional[dict] = None) -> dict:
    """Run discovery and return a schema-valid discovery dict."""
    if mode not in ("fast", "slow"):
        raise ValueError("mode must be 'fast' or 'slow'")
    config = config or EdgeConfig()
    profiles = _addressing_profiles(config)

    result: dict = {"schema_version": SCHEMA_VERSION}
    if bus is not None:
        result["bus"] = bus
    result["obd"] = _discover_obd(transport, config, profiles, slow=(mode == "slow"))
    result["uds"] = _discover_uds(transport, config, profiles, slow=(mode == "slow"))
    if mode == "slow":
        result["plain_can"] = plain_can_census(transport, config.plain_can_census_s)
    return result


# --------------------------------------------------------------------------- #
# OBD
# --------------------------------------------------------------------------- #
def _obd_supported_functional(transport, prof: Addressing, timeout: float
                              ) -> Dict[int, set]:
    """Broadcast the supported-PID base queries; return {responder_id: {pids}}.

    Only 8 requests (bases 0x00..0xE0) regardless of continuation, which keeps
    multi-ECU enumeration cheap and robust on a functional address.
    """
    responders: Dict[int, set] = defaultdict(set)
    for i, base in enumerate(SUPPORT_QUERY_PIDS):
        for rid, pdu in transport.request_all(
                prof.functional_tx, bytes([0x01, base]),
                timeout=timeout, expect_ids=prof.response_ids):
            if len(pdu) >= 6 and pdu[0] == 0x41 and pdu[1] == base:
                for p in parse_supported_pids(base, pdu[2:]):
                    if p not in SUPPORT_QUERY_PIDS:
                        responders[rid].add(p)
        # If the very first supported-PID query finds nobody, no OBD stack is
        # listening on this addressing -- skip the remaining base queries.
        if i == 0 and not responders:
            break
    return responders


def _discover_obd(transport, config: EdgeConfig, profiles: List[Addressing],
                  slow: bool) -> dict:
    timeout = config.request_timeout_s
    multi = hasattr(transport, "request_all")

    responders: List[dict] = []      # per-ECU detail
    all_pids: set = set()
    samples: List[dict] = []
    addressing_tried = [p.name for p in profiles]

    if multi:
        for prof in profiles:
            # {responder_id: {pids}} gathered from BOTH functional broadcast and
            # a physical ECU sweep (some vehicles answer only one of them).
            found: Dict[int, set] = defaultdict(set)

            # (a) functional broadcast -- supported-PID bitmasks are single frames.
            for rid, pids in _obd_supported_functional(
                    transport, prof, min(timeout, 0.3)).items():
                found[rid].update(pids)

            # (b) physical ECU sweep, liveness-gated on PID 0x00 (short timeout;
            # a dead address costs one probe).
            for tx, rx in _physical_pairs(prof, slow):
                c = ObdClient(transport, tx_id=tx, rx_id=rx, timeout=min(timeout, 0.2))
                if c.query_raw(0x00, mode=0x01) is None:
                    continue
                found[rx].update(c.enumerate_supported_pids())

            for rid in sorted(found):
                req_id = req_id_for_response(rid, prof.is_extended)
                client = ObdClient(transport, tx_id=req_id, rx_id=rid, timeout=timeout)
                pids = set(found[rid])
                if slow:
                    for pid in range(config.obd_pid_min, config.obd_pid_max + 1):
                        if pid in pids or pid in SUPPORT_QUERY_PIDS:
                            continue
                        if client.query_raw(pid, mode=0x01) is not None:
                            pids.add(pid)
                        time.sleep(config.brute_force_throttle_s)
                ecu_samples = _sample_pids(client, sorted(pids), rid, prof)
                samples.extend(ecu_samples)
                all_pids.update(pids)
                responders.append({
                    "addressing": prof.name,
                    "rx_id": _hexid(rid, prof.is_extended),
                    "tx_id": _hexid(req_id, prof.is_extended),
                    "supported_pids": [pid_hex(p) for p in sorted(pids)],
                })
    else:
        # Legacy single-responder path (SimulatedTransport / ELM).
        client = ObdClient(transport, tx_id=OBD_FUNCTIONAL_TX, rx_id=OBD_RESP_BASE,
                           timeout=timeout)
        supported = client.enumerate_supported_pids()
        if slow:
            for pid in range(config.obd_pid_min, config.obd_pid_max + 1):
                if pid in supported:
                    continue
                if client.query_raw(pid, mode=0x01) is not None:
                    supported.append(pid)
                time.sleep(config.brute_force_throttle_s)
        supported = sorted(set(supported))
        samples.extend(_sample_pids(client, supported, OBD_RESP_BASE, None))
        all_pids.update(supported)
        if supported:
            responders.append({
                "addressing": "11bit",
                "rx_id": f"0x{OBD_RESP_BASE:03X}",
                "tx_id": f"0x{OBD_FUNCTIONAL_TX:03X}",
                "supported_pids": [pid_hex(p) for p in supported],
            })

    return {
        "supported_pids": [pid_hex(p) for p in sorted(all_pids)],
        "samples": samples,
        "responders": responders,
        "addressing_tried": addressing_tried,
    }


def _sample_pids(client: ObdClient, pids: List[int], rid: int,
                 prof: Optional[Addressing]) -> List[dict]:
    out: List[dict] = []
    for pid in pids:
        sample = client.sample_pid(pid)
        if sample is not None:
            sample["t_utc"] = time.time()
            if prof is not None:
                sample["addressing"] = prof.name
                sample["ecu"] = _hexid(rid, prof.is_extended)
            out.append(sample)
    return out


# --------------------------------------------------------------------------- #
# UDS
# --------------------------------------------------------------------------- #
def _discover_uds(transport, config: EdgeConfig, profiles: List[Addressing],
                  slow: bool) -> dict:
    timeout = config.request_timeout_s
    multi = hasattr(transport, "request_all")
    ecus: List[dict] = []
    all_responding: set = set()
    addressing_tried = [p.name for p in profiles]

    catalog_dids = list(DID_CATALOG.keys())
    brute_dids = (list(range(config.uds_did_min, config.uds_did_max + 1))
                  if slow else [])

    # Fast probes only need a short window (a response arrives in a few ms); the
    # full timeout is reserved for multi-frame catalog reads. A silent physical
    # address costs one probe_timeout, so keep it tight -- the sweep visits many.
    probe_timeout = min(timeout, 0.2)

    # DIDs almost any UDS ECU answers -- used to decide if an addressing scheme
    # has *anyone* home before spending the full catalog/brute budget on it.
    # VIN (0xF190) first: it is near-universal, so one probe settles liveness.
    liveness_dids = [0xF190, 0xF187, 0xF18C, 0xF195, 0xF186]

    if multi:
        for prof in profiles:
            # 1) Find live responder ids from BOTH functional broadcast and a
            #    physical ECU sweep -- a vehicle may answer only one of them.
            live_rids: set = set()

            for did in liveness_dids[:2]:  # functional liveness
                pdu = bytes([0x22, (did >> 8) & 0xFF, did & 0xFF])
                for rid, resp in transport.request_all(
                        prof.functional_tx, pdu, timeout=probe_timeout,
                        expect_ids=prof.response_ids):
                    if len(resp) >= 3 and resp[0] == 0x62:
                        live_rids.add(rid)

            # Physical liveness: VIN first (near-universal); only if that is
            # silent try one more DID, so a dead address costs ~one timeout.
            for tx, rx in _physical_pairs(prof, slow):
                c = UdsClient(transport, tx_id=tx, rx_id=rx, timeout=probe_timeout)
                probe = liveness_dids if slow else liveness_dids[:2]
                if any(c.read_data_by_identifier(d) is not None for d in probe):
                    live_rids.add(rx)

            # 2) Read the catalog (+ brute in slow mode) from each live ECU,
            #    addressed physically -- the reliable path.
            for rid in sorted(live_rids):
                req_id = req_id_for_response(rid, prof.is_extended)
                c = UdsClient(transport, tx_id=req_id, rx_id=rid,
                              timeout=min(timeout, 0.6))
                values: Dict[int, bytes] = {}
                for did in catalog_dids:
                    v = c.read_data_by_identifier(did)
                    if v is not None:
                        values[did] = v
                if brute_dids:
                    for did in brute_dids:
                        if did in values:
                            continue
                        v = c.read_data_by_identifier(did)
                        if v is not None:
                            values[did] = v
                        time.sleep(config.brute_force_throttle_s)
                # DTCs (0x19) are read-only and permitted in the default session.
                dtcs = c.read_dtcs(0xFF)
                entry = {
                    "addressing": prof.name,
                    "tx_id": _hexid(req_id, prof.is_extended),
                    "rx_id": _hexid(rid, prof.is_extended),
                    "dids": [did_hex(d) for d in sorted(values)],
                    "values": {did_hex(d): _decode_did_value(values[d])
                               for d in sorted(values)},
                }
                if dtcs:
                    entry["dtc_count"] = len(dtcs)
                    entry["dtcs"] = [{"code": decode_dtc(d), "raw": f"0x{d:06X}",
                                      "status": f"0x{s:02X}"} for d, s in dtcs]
                if not values and not dtcs:
                    continue
                ecus.append(entry)
                all_responding.update(values)
    else:
        # Legacy single-responder path (SimulatedTransport / ELM), 11-bit only.
        for tx_id, rx_id in STANDARD_ECUS:
            client = UdsClient(transport, tx_id=tx_id, rx_id=rx_id, timeout=timeout)
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
                    "addressing": "11bit",
                    "tx_id": f"0x{tx_id:03X}",
                    "rx_id": f"0x{rx_id:03X}",
                    "dids": [did_hex(d) for d in responding],
                })
                all_responding.update(responding)

    return {
        "responding_dids": [did_hex(d) for d in sorted(all_responding)],
        "ecus": ecus,
        "addressing_tried": addressing_tried,
    }


def _decode_did_value(data: bytes) -> str:
    """Best-effort human-readable rendering of a DID value (ASCII or hex)."""
    if data and all(0x20 <= b < 0x7F for b in data):
        return data.decode("ascii")
    return data.hex()


def _hexid(arb_id: int, is_extended: bool) -> str:
    return f"0x{arb_id:08X}" if is_extended else f"0x{arb_id:03X}"


# --------------------------------------------------------------------------- #
# Plain-CAN census (passive)
# --------------------------------------------------------------------------- #
def plain_can_census(transport: Transport, seconds: float) -> dict:
    """Passively sniff the bus and summarise every arbitration id seen.

    For each id: how often it appears, its estimated period, which byte
    positions ever change, the width, whether it is extended, and an ASCII
    rendering of a representative payload (useful for spotting VIN/text frames).
    """
    counts: Dict[int, int] = defaultdict(int)
    first_t: Dict[int, float] = {}
    last_t: Dict[int, float] = {}
    first_data: Dict[int, bytes] = {}
    last_data: Dict[int, bytes] = {}
    changing: Dict[int, set] = defaultdict(set)
    extended: Dict[int, bool] = {}
    max_dlc: Dict[int, int] = defaultdict(int)

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        for frame in transport.recv_frames(min(0.5, max(0.01, remaining))):
            if frame.direction != "rx":
                continue
            aid = frame.arb_id
            counts[aid] += 1
            extended[aid] = frame.is_extended
            max_dlc[aid] = max(max_dlc[aid], len(frame.data))
            last_data[aid] = frame.data
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
            "arb_id": _hexid(aid, extended.get(aid, False)),
            "is_extended": bool(extended.get(aid, False)),
            "count": n,
            "dlc": max_dlc[aid],
            "changing_bytes": sorted(changing[aid]),
            "sample_hex": last_data.get(aid, b"").hex(),
            "sample_ascii": _ascii_preview(last_data.get(aid, b"")),
        }
        if n > 1 and span > 0:
            entry["period_ms_est"] = round(span / (n - 1) * 1000.0, 3)
        arb_ids.append(entry)

    return {"arb_ids": arb_ids}


def _ascii_preview(data: bytes) -> str:
    return "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in data)


# Backwards-compatible alias for the previous private name.
def _plain_can_census(transport: Transport, config: EdgeConfig) -> dict:
    return plain_can_census(transport, config.plain_can_census_s)
