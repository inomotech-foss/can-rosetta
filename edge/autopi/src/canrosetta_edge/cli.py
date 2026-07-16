"""Command-line interface: ``canrosetta-edge``.

Subcommands:
  discover   run Stage 1a and write can/discovery.json (+ manifest)
  log        run Stage 1b continuous capture into can/frames.{parquet,jsonl}
  run        discover, then log (the normal in-vehicle flow)
  simulate   end-to-end demo against the built-in SimulatedTransport
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

from .config import EdgeConfig
from .discovery import discover
from .logging_ import Poller, capture, make_frame_writer
from .obd import PIDS
from .session import (
    SessionLayout,
    build_manifest,
    frames_stream_entry,
    new_session_id,
    write_discovery,
    write_manifest,
)
from .transport import (
    ElmTransport,
    SimulatedTransport,
    SocketCanTransport,
    Transport,
)


def make_transport(config: EdgeConfig, override: Optional[str] = None) -> Transport:
    kind = override or config.transport
    if kind == "simulated":
        return SimulatedTransport(channel=config.channel)
    if kind == "socketcan":
        return SocketCanTransport(channel=config.channel, bitrate=config.bitrate)
    if kind == "elm":
        return ElmTransport(port=config.elm_port, baudrate=config.elm_baudrate,
                            channel=config.channel)
    raise ValueError(f"unknown transport '{kind}'")


def _load_config(args) -> EdgeConfig:
    config = EdgeConfig.from_yaml(args.config) if args.config else EdgeConfig()
    if getattr(args, "transport", None):
        config.transport = args.transport
    if getattr(args, "channel", None):
        config.channel = args.channel
    if getattr(args, "output_dir", None):
        config.output_dir = args.output_dir
    return config


def _supported_poll_pids(discovery_result: dict):
    """PIDs to poll during logging: discovered ones we can decode."""
    pids = []
    for s in discovery_result.get("obd", {}).get("supported_pids", []):
        try:
            pid = int(s, 16)
        except ValueError:
            continue
        if pid in PIDS:
            pids.append(pid)
    return pids


def _run_discover(config: EdgeConfig, layout: SessionLayout, mode: str) -> dict:
    with make_transport(config) as transport:
        result = discover(transport, mode=mode, config=config)
    write_discovery(layout.discovery_path, result)
    return result


def cmd_discover(args) -> int:
    config = _load_config(args)
    session_id = args.session_id or new_session_id()
    layout = SessionLayout(config.output_dir, session_id).ensure()
    result = _run_discover(config, layout, mode=args.mode)

    streams = [{"path": "can/discovery.json", "kind": "discovery"}]
    manifest = build_manifest(session_id, device_id=args.device_id, streams=streams)
    write_manifest(layout.manifest_path, manifest)

    n_pids = len(result.get("obd", {}).get("supported_pids", []))
    n_dids = len(result.get("uds", {}).get("responding_dids", []))
    print(f"[discover] session={session_id} mode={args.mode} "
          f"OBD PIDs={n_pids} UDS DIDs={n_dids}")
    print(f"[discover] wrote {layout.discovery_path}")
    return 0


def cmd_log(args) -> int:
    config = _load_config(args)
    session_id = args.session_id or new_session_id()
    layout = SessionLayout(config.output_dir, session_id).ensure()

    poller = None
    discovery_result = None
    disc_path = layout.discovery_path
    if os.path.exists(disc_path):
        import json
        with open(disc_path) as fh:
            discovery_result = json.load(fh)

    with make_transport(config) as transport:
        writer = make_frame_writer(layout.frames_base, config.prefer_parquet)
        if discovery_result is not None:
            pids = _supported_poll_pids(discovery_result)
            if pids:
                poller = Poller(transport, pids, channel=config.channel,
                                timeout=config.request_timeout_s)
        try:
            n = capture(transport, writer, duration=args.duration or config.log_duration_s,
                        poller=poller, poll_rate_hz=config.poll_rate_hz)
        finally:
            writer.close()

    _finalize_log(layout, session_id, args, writer, poller, discovery_result)
    print(f"[log] session={session_id} wrote {n} frames -> {writer.path}")
    return 0


def _finalize_log(layout, session_id, args, writer, poller, discovery_result):
    # Fold polled reference samples into discovery.json.
    if poller is not None and poller.samples:
        if discovery_result is None:
            discovery_result = {"schema_version": "1.0.0"}
        obd = discovery_result.setdefault("obd", {})
        obd.setdefault("samples", []).extend(poller.samples)
        write_discovery(layout.discovery_path, discovery_result)

    rel = os.path.relpath(writer.path, layout.root)
    streams = [frames_stream_entry(rel, writer)]
    if os.path.exists(layout.discovery_path):
        streams.append({"path": "can/discovery.json", "kind": "discovery"})
    manifest = build_manifest(session_id, device_id=args.device_id, streams=streams)
    write_manifest(layout.manifest_path, manifest)


def cmd_run(args) -> int:
    config = _load_config(args)
    session_id = args.session_id or new_session_id()
    layout = SessionLayout(config.output_dir, session_id).ensure()

    result = _run_discover(config, layout, mode=args.mode)
    print(f"[run] discovery done: "
          f"{len(result.get('obd', {}).get('supported_pids', []))} PIDs")

    with make_transport(config) as transport:
        writer = make_frame_writer(layout.frames_base, config.prefer_parquet)
        pids = _supported_poll_pids(result)
        poller = Poller(transport, pids, channel=config.channel,
                        timeout=config.request_timeout_s) if pids else None
        try:
            n = capture(transport, writer,
                        duration=args.duration or config.log_duration_s,
                        poller=poller, poll_rate_hz=config.poll_rate_hz)
        finally:
            writer.close()

    _finalize_log(layout, session_id, args, writer, poller, result)
    print(f"[run] session={session_id} logged {n} frames -> {writer.path}")
    return 0


def cmd_simulate(args) -> int:
    """Self-contained demo: force the simulated transport, short duration."""
    config = _load_config(args)
    config.transport = "simulated"
    if args.duration is None:
        args.duration = 3.0
    session_id = args.session_id or new_session_id()
    layout = SessionLayout(config.output_dir, session_id).ensure()

    result = _run_discover(config, layout, mode=args.mode)
    print("[simulate] discovered OBD PIDs:",
          ", ".join(result["obd"]["supported_pids"]))
    print("[simulate] responding UDS DIDs:",
          ", ".join(result["uds"]["responding_dids"]) or "(none)")

    with make_transport(config) as transport:
        writer = make_frame_writer(layout.frames_base, config.prefer_parquet)
        pids = _supported_poll_pids(result)
        poller = Poller(transport, pids, channel=config.channel) if pids else None
        try:
            n = capture(transport, writer, duration=args.duration,
                        poller=poller, poll_rate_hz=config.poll_rate_hz)
        finally:
            writer.close()

    _finalize_log(layout, session_id, args, writer, poller, result)
    print(f"[simulate] logged {n} frames over {args.duration:.1f}s -> {writer.path}")
    print(f"[simulate] session written under {layout.root}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="canrosetta-edge",
                                description="CAN-Rosetta edge (in-vehicle) tool")
    p.add_argument("--config", help="path to a YAML config file")

    sub = p.add_subparsers(dest="command", required=True)

    def common(sp):
        sp.add_argument("--transport", choices=["simulated", "socketcan", "elm"])
        sp.add_argument("--channel")
        sp.add_argument("--output-dir", dest="output_dir")
        sp.add_argument("--session-id", dest="session_id")
        sp.add_argument("--device-id", dest="device_id")

    sp = sub.add_parser("discover", help="Stage 1a discovery")
    common(sp)
    sp.add_argument("--mode", choices=["fast", "slow"], default="fast")
    sp.set_defaults(func=cmd_discover)

    sp = sub.add_parser("log", help="Stage 1b continuous capture")
    common(sp)
    sp.add_argument("--duration", type=float, help="seconds (default: until Ctrl-C)")
    sp.set_defaults(func=cmd_log)

    sp = sub.add_parser("run", help="discover then log")
    common(sp)
    sp.add_argument("--mode", choices=["fast", "slow"], default="fast")
    sp.add_argument("--duration", type=float)
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("simulate", help="demo against SimulatedTransport")
    common(sp)
    sp.add_argument("--mode", choices=["fast", "slow"], default="fast")
    sp.add_argument("--duration", type=float)
    sp.set_defaults(func=cmd_simulate)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:  # pragma: no cover
        print("\n[interrupted]", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
