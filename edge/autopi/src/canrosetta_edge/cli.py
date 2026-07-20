"""Command-line interface: ``canrosetta-edge``.

Subcommands:
  discover   run Stage 1a and write can/discovery.json (+ manifest)
  log        run Stage 1b continuous capture (+ onboard IMU/GPS) into the session
  run        discover, then log (the normal in-vehicle flow)
  simulate   end-to-end demo against the built-in SimulatedTransport
  serve      run the local control server so the companion phone can steer this device

All flows go through :class:`canrosetta_edge.engine.Engine`, so they all log the
AutoPi's onboard sensors beside the CAN bus and hold the device awake while
running (see docs/control-protocol.md).
"""

from __future__ import annotations

import argparse
import sys
import time

from .config import EdgeConfig
from .engine import LOGGING, Engine, make_transport  # noqa: F401 (make_transport re-exported)


def _load_config(args) -> EdgeConfig:
    config = EdgeConfig.from_yaml(args.config) if args.config else EdgeConfig()
    for attr in ("transport", "channel", "output_dir", "control_host",
                 "control_port", "control_token"):
        val = getattr(args, attr, None)
        if val is not None:
            setattr(config, attr, val)
    return config


def _engine(args) -> Engine:
    config = _load_config(args)
    eng = Engine(config, device_id=getattr(args, "device_id", None))
    eng.start_session(session_id=getattr(args, "session_id", None))
    return eng


def _print_status(eng: Engine, tag: str) -> None:
    st = eng.status()
    summary = st.get("discovery_summary") or {}
    print(f"[{tag}] session={st['session_id']} state={st['state']} "
          f"OBD PIDs={summary.get('obd_pids', 0)} UDS DIDs={summary.get('uds_dids', 0)} "
          f"frames={st['stats']['frames']}")


def _await_or_interrupt(eng: Engine, duration) -> None:
    """Wait for a fixed-duration job, or run until Ctrl-C then stop cleanly."""
    if duration is not None:
        eng.wait()
        return
    try:
        while eng.state == LOGGING:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[stopping]", file=sys.stderr)
        eng.stop()


def cmd_discover(args) -> int:
    eng = _engine(args)
    eng.start_discovery(args.mode)
    eng.wait()
    _print_status(eng, "discover")
    return 0 if eng.error is None else 1


def cmd_log(args) -> int:
    eng = _engine(args)
    eng.start_logging(args.duration)
    _await_or_interrupt(eng, args.duration)
    _print_status(eng, "log")
    return 0 if eng.error is None else 1


def cmd_run(args) -> int:
    eng = _engine(args)
    eng.start_run(args.mode, args.duration)
    _await_or_interrupt(eng, args.duration)
    _print_status(eng, "run")
    return 0 if eng.error is None else 1


def cmd_simulate(args) -> int:
    args.transport = "simulated"
    if args.duration is None:
        args.duration = 3.0
    eng = _engine(args)
    eng.start_run(args.mode, args.duration)
    eng.wait()
    st = eng.status()
    print(f"[simulate] session written under {st['output_dir']}")
    _print_status(eng, "simulate")
    return 0 if eng.error is None else 1


def cmd_recon(args) -> int:
    """Reverse-engineering recon: detect speed, census plain CAN, scan OBD/UDS."""
    import json
    import os

    from .recon import format_report, run_recon
    from .session import SessionLayout, new_session_id, write_discovery

    config = _load_config(args)
    config.transport = "socketcan"
    if args.interface:
        config.channel = args.interface
        config.interfaces = args.interface
    if args.bitrate and args.bitrate != "auto":
        config.bitrate = int(args.bitrate)
        config.bitrate_autodetect = False
    if args.no_autodetect:
        config.bitrate_autodetect = False
    if args.diag_addressing:
        config.diag_addressing = args.diag_addressing
    if args.census_s is not None:
        config.plain_can_census_s = args.census_s
    if args.allow_session:
        config.allow_active_session = True

    mode = "slow" if args.deep else "fast"
    result = run_recon(config, mode=mode)

    if not args.no_report:
        print(format_report(result, top_n=args.top))

    # Persist a schema-valid discovery.json (+ raw dump) under a session dir.
    session_id = getattr(args, "session_id", None) or new_session_id()
    layout = SessionLayout(os.path.join(config.output_dir, session_id), session_id).ensure()
    write_discovery(layout.discovery_path, result)
    print(f"\n[recon] discovery written to {layout.discovery_path}", file=sys.stderr)
    return 0


def cmd_serve(args) -> int:
    from .control import serve  # lazy: needs aiohttp
    from .pairing import format_pairing

    config = _load_config(args)
    # headless AutoPi has no screen — print the pairing QR + host/token to the console
    print(format_pairing(config))
    eng = Engine(config, device_id=getattr(args, "device_id", None))
    serve(eng, host=config.control_host, port=config.control_port,
          token=config.control_token)
    return 0


def cmd_pairing(args) -> int:
    from .pairing import format_pairing

    print(format_pairing(_load_config(args)))
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

    sp = sub.add_parser("log", help="Stage 1b continuous capture (+ onboard sensors)")
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

    sp = sub.add_parser("recon",
                        help="reverse-engineering recon: detect CAN speed, census "
                             "plain CAN, scan readable OBD/UDS signals")
    common(sp)
    sp.add_argument("--interface", help="CAN interface (default: auto-detect all can*)")
    sp.add_argument("--bitrate", default="auto",
                    help="'auto' (default) or a fixed bitrate, e.g. 500000")
    sp.add_argument("--no-autodetect", action="store_true",
                    help="trust --interface/--bitrate, skip bitrate probing")
    sp.add_argument("--diag-addressing", dest="diag_addressing",
                    choices=["11bit", "29bit", "both"],
                    help="which OBD/UDS addressing to probe (default: both)")
    sp.add_argument("--deep", action="store_true",
                    help="slow mode: brute-force OBD PIDs + UDS DIDs (throttled)")
    sp.add_argument("--allow-session", dest="allow_session", action="store_true",
                    help="INTRUSIVE: open a UDS extended session (0x10) on live "
                         "ECUs. Stationary vehicle only — see SAFETY.md.")
    sp.add_argument("--census-s", dest="census_s", type=float,
                    help="passive plain-CAN census window in seconds")
    sp.add_argument("--top", type=int, default=40,
                    help="how many plain-CAN ids to show in the report")
    sp.add_argument("--no-report", action="store_true",
                    help="suppress the text report (still writes discovery.json)")
    sp.set_defaults(func=cmd_recon)

    sp = sub.add_parser("serve", help="run the control server (phone steers the device)")
    common(sp)
    sp.add_argument("--control-host", dest="control_host")
    sp.add_argument("--control-port", dest="control_port", type=int)
    sp.add_argument("--control-token", dest="control_token")
    sp.set_defaults(func=cmd_serve)

    sp = sub.add_parser("pairing",
                        help="print the pairing host/token + a terminal QR (headless setup)")
    common(sp)
    sp.add_argument("--control-port", dest="control_port", type=int)
    sp.add_argument("--control-token", dest="control_token")
    sp.set_defaults(func=cmd_pairing)
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
