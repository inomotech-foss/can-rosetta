"""Reverse-engineering recon pipeline for the edge (AutoPi).

Ties together the three questions a first-contact investigation of an unknown
vehicle bus needs answered:

1. **What is the CAN speed?** -- :func:`canbus.find_active_bus` probes the
   AutoPi's CAN interfaces and bitrates (passively, listen-only) and picks the
   live one.
2. **What plain-CAN messages are broadcast?** -- a passive census of every
   arbitration id (period, width, changing bytes, ASCII preview).
3. **Which OBD/UDS signals are readable?** -- the catalog scan over both 11-bit
   and 29-bit diagnostic addressing.

The output is the same schema-valid ``discovery.json`` dict the rest of the
pipeline consumes, plus a human-readable text report for the SSH console.

Everything here is read-only (see SAFETY.md): passive sniffing plus OBD 0x01/0x09
and UDS 0x22 requests, all funnelled through the guarded clients.
"""

from __future__ import annotations

import time
from typing import List, Optional

from . import canbus
from .config import EdgeConfig
from .discovery import discover, plain_can_census
from .obd import PIDS
from .transport import NativeSocketCanTransport


def _parse_interfaces(config: EdgeConfig) -> Optional[List[str]]:
    spec = (config.interfaces or "auto").strip()
    if spec.lower() == "auto":
        return None
    return [s.strip() for s in spec.split(",") if s.strip()]


def detect_bus(config: EdgeConfig) -> canbus.BusScan:
    """Answer 'which bus, what speed?' using the config's detection knobs."""
    if not config.bitrate_autodetect:
        # Trust the configured channel/bitrate; just sample it.
        frames, _, uids = canbus._count_traffic(config.channel, config.detect_window_s)
        return canbus.BusScan(config.channel,
                              config.bitrate if frames else None,
                              frames, uids, used_existing=True)
    return canbus.find_active_bus(
        interfaces=_parse_interfaces(config),
        bitrates=canbus.DEFAULT_BITRATES,
        window_s=config.detect_window_s,
    )


def _open_transport(scan: canbus.BusScan, config: EdgeConfig) -> NativeSocketCanTransport:
    """Bring the detected bus up for active probing and return an open transport."""
    interface = scan.interface or config.channel
    if not scan.used_existing and scan.bitrate:
        # We probed listen-only and left it down; bring it up in normal mode so
        # OBD/UDS requests can be transmitted and ACKed.
        canbus.configure_bitrate(interface, scan.bitrate, listen_only=False)
        time.sleep(0.2)
    return NativeSocketCanTransport(channel=interface,
                                    bitrate=scan.bitrate or config.bitrate).open()


def run_recon(config: Optional[EdgeConfig] = None, mode: str = "fast",
              allow_session: Optional[bool] = None) -> dict:
    """Run the full recon and return the discovery dict (with a ``bus`` block)."""
    config = config or EdgeConfig()
    if allow_session is not None:
        config.allow_active_session = allow_session
    scan = detect_bus(config)

    bus_info = {
        "interface": scan.interface,
        "bitrate": scan.bitrate,
        "bitrate_source": "existing" if scan.used_existing else "detected",
        "frames_sampled": scan.frames,
        "unique_ids": scan.unique_ids,
        "candidates": [
            {"interface": c.interface, "bitrate": c.bitrate,
             "frames": c.frames, "errors": c.errors, "unique_ids": c.unique_ids}
            for c in scan.candidates
        ],
    }

    if not scan.active:
        # No live bus: still emit a valid (empty) discovery so the caller can
        # report the negative result instead of crashing.
        return {
            "schema_version": "1.0.0",
            "bus": bus_info,
            "obd": {"supported_pids": [], "samples": [], "responders": [],
                    "addressing_tried": []},
            "uds": {"responding_dids": [], "ecus": [], "addressing_tried": []},
            "plain_can": {"arb_ids": []},
        }

    transport = _open_transport(scan, config)
    try:
        result = discover(transport, mode=mode, config=config, bus=bus_info)
        # Always census (it answers "identify plain CAN messages"), even in fast
        # mode where discover() would otherwise skip it.
        if "plain_can" not in result:
            result["plain_can"] = plain_can_census(transport, config.plain_can_census_s)
        # Opt-in intrusive step: try to open an extended session on the ECUs that
        # answered read-only, and see what it unlocks. OFF unless authorised.
        if config.allow_active_session:
            result["active_probe"] = _run_active_probe(transport, result)
    finally:
        transport.close()
    return result


def _run_active_probe(transport, result: dict) -> dict:
    """Attempt extended sessions on the live UDS ECUs (intrusive; opt-in)."""
    from .active import probe_extended_session

    ecus = []
    for e in result.get("uds", {}).get("ecus", []):
        try:
            tx = int(e["tx_id"], 16)
            rx = int(e["rx_id"], 16)
        except (KeyError, ValueError):
            continue
        ecus.append((tx, rx))
    if not ecus:
        return {"attempted": False, "reason": "no live UDS ECUs to probe"}
    # DIDs worth retrying once a session is open (manufacturer/live-data ranges).
    dids = [0xF190, 0xF191, 0xF192, 0xF194, 0xF1A2] + list(range(0x0100, 0x0110))
    ecu_results = probe_extended_session(transport, ecus, allow=True, dids=dids)
    return {"attempted": True, "ecus": ecu_results}


# --------------------------------------------------------------------------- #
# Human-readable report
# --------------------------------------------------------------------------- #
def format_report(result: dict, top_n: int = 40) -> str:
    lines: List[str] = []
    add = lines.append

    bus = result.get("bus", {})
    add("=" * 68)
    add("CAN-Rosetta recon report")
    add("=" * 68)
    br = bus.get("bitrate")
    add(f"Bus        : {bus.get('interface','?')} @ "
        f"{br if br else 'UNKNOWN'} bit/s ({bus.get('bitrate_source','?')})")
    add(f"Live check : {bus.get('frames_sampled',0)} frames, "
        f"{bus.get('unique_ids',0)} unique ids sampled")
    cands = bus.get("candidates") or []
    if len(cands) > 1:
        detail = ", ".join(f"{c['bitrate']}:{c['frames']}f/{c['errors']}e" for c in cands)
        add(f"Bitrate scan: {detail}")
    add("")

    # OBD
    obd = result.get("obd", {})
    pids = obd.get("supported_pids", [])
    add(f"OBD-II     : {len(pids)} supported PID(s)  "
        f"[addressing tried: {', '.join(obd.get('addressing_tried', [])) or 'n/a'}]")
    for resp in obd.get("responders", []):
        add(f"  ECU {resp.get('rx_id')} [{resp.get('addressing')}]: "
            f"{len(resp.get('supported_pids', []))} PIDs")
    latest = {}
    for s in obd.get("samples", []):
        latest[s["pid"]] = s
    for pid_hex_str in pids:
        s = latest.get(pid_hex_str, {})
        name = s.get("name") or _pid_name(pid_hex_str)
        val = s.get("value")
        unit = s.get("unit", "")
        shown = f"{val} {unit}".strip() if val is not None else f"raw={s.get('raw','?')}"
        add(f"    {pid_hex_str} {name:<26} {shown}")
    if not pids:
        add("    (no OBD PIDs answered — common for EVs; see plain-CAN below)")
    add("")

    # UDS
    uds = result.get("uds", {})
    ecus = uds.get("ecus", [])
    add(f"UDS 0x22   : {len(uds.get('responding_dids', []))} DID(s) across "
        f"{len(ecus)} ECU(s)  [addressing tried: "
        f"{', '.join(uds.get('addressing_tried', [])) or 'n/a'}]")
    for e in ecus:
        add(f"  ECU rx {e.get('rx_id')} tx {e.get('tx_id')} [{e.get('addressing')}]:")
        values = e.get("values", {})
        for d in e.get("dids", []):
            v = values.get(d, "")
            add(f"    {d}  {v}")
        if e.get("dtc_count"):
            codes = ", ".join(d["code"] for d in e.get("dtcs", [])[:12])
            more = "" if e["dtc_count"] <= 12 else f", +{e['dtc_count'] - 12} more"
            add(f"    DTCs ({e['dtc_count']}): {codes}{more}")
    if not ecus:
        add("    (no UDS responders on this bus)")
    add("")

    # Intrusive session probe (only present when --allow-session was used)
    ap = result.get("active_probe")
    if ap:
        add("Session    : extended-session probe (INTRUSIVE)")
        if not ap.get("attempted"):
            add(f"    {ap.get('reason', 'not attempted')}")
        for e in ap.get("ecus", []):
            s = e.get("session", {})
            if s.get("opened"):
                unlocked = e.get("unlocked_dids", {})
                add(f"    {e.get('rx_id')}: OPENED — {len(unlocked)} extra DID(s): "
                    f"{', '.join(unlocked) or 'none'}")
            else:
                detail = s.get("nrc") or s.get("result", "?")
                add(f"    {e.get('rx_id')}: refused ({detail})")
        add("")

    # Plain CAN
    arb = result.get("plain_can", {}).get("arb_ids", [])
    arb_sorted = sorted(arb, key=lambda e: e.get("count", 0), reverse=True)
    add(f"Plain CAN  : {len(arb)} arbitration id(s) observed "
        f"(top {min(top_n, len(arb))} by frequency)")
    add(f"    {'id':<12}{'per(ms)':>9}{'dlc':>5}{'cnt':>7}  changing        ascii")
    for e in arb_sorted[:top_n]:
        per = e.get("period_ms_est")
        per_s = f"{per:.1f}" if per is not None else "-"
        chg = ",".join(str(b) for b in e.get("changing_bytes", []))
        add(f"    {e['arb_id']:<12}{per_s:>9}{e.get('dlc',0):>5}{e.get('count',0):>7}"
            f"  {chg:<15} {e.get('sample_ascii','')}")
    add("=" * 68)
    return "\n".join(lines)


def _pid_name(pid_hex_str: str) -> str:
    try:
        pid = int(pid_hex_str, 16)
    except (ValueError, TypeError):
        return ""
    p = PIDS.get(pid)
    return p.name if p else ""
