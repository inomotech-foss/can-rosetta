#!/usr/bin/env python3
"""Standalone CAN reverse-engineering tool for the AutoPi (no install needed).

Run it straight off an SSH session on the AutoPi -- it needs nothing but Python 3
and a SocketCAN interface (no ``pip``, no ``python-can``):

    python3 reverse_engineer.py                 # auto-detect bus + speed, fast scan
    python3 reverse_engineer.py --deep          # + brute-force OBD PIDs / UDS DIDs
    python3 reverse_engineer.py --interface can0 --bitrate 500000
    python3 reverse_engineer.py --census-s 30   # longer plain-CAN census
    python3 reverse_engineer.py --json out.json # also dump the raw discovery dict

It answers the three first-contact questions about an unknown vehicle bus:

  1. What is the CAN speed?              (passive listen-only bitrate detection)
  2. What plain-CAN messages exist?      (passive per-arb-id census)
  3. Which OBD/UDS signals are readable? (11-bit + 29-bit catalog scan)

Read-only by design (see SAFETY.md): passive sniffing + OBD 0x01/0x09 and UDS
0x22 reads only. Bitrate probing may need root (sudo) to run ``ip link``; an
already-up interface is used as-is without touching it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Make the package importable whether or not it was pip-installed.
_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "src")
if os.path.isdir(_SRC) and _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from canrosetta_edge.config import EdgeConfig  # noqa: E402
from canrosetta_edge.recon import format_report, run_recon  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--interface", help="CAN interface (default: auto-detect all can*)")
    ap.add_argument("--bitrate", default="auto",
                    help="'auto' (default) or a fixed bitrate, e.g. 500000")
    ap.add_argument("--no-autodetect", action="store_true",
                    help="trust --interface/--bitrate; skip bitrate probing")
    ap.add_argument("--diag-addressing", choices=["11bit", "29bit", "both"],
                    default="both", help="OBD/UDS addressing to probe (default: both)")
    ap.add_argument("--deep", action="store_true",
                    help="brute-force OBD PIDs 0x00-0xFF and UDS DIDs (throttled)")
    ap.add_argument("--census-s", type=float, default=15.0,
                    help="passive plain-CAN census window in seconds (default: 15)")
    ap.add_argument("--top", type=int, default=60,
                    help="how many plain-CAN ids to show (default: 60)")
    ap.add_argument("--json", help="also write the raw discovery dict to this path")
    args = ap.parse_args(argv)

    config = EdgeConfig()
    config.transport = "socketcan"
    config.diag_addressing = args.diag_addressing
    config.plain_can_census_s = args.census_s
    if args.interface:
        config.channel = args.interface
        config.interfaces = args.interface
    if args.bitrate != "auto":
        config.bitrate = int(args.bitrate)
        config.bitrate_autodetect = False
    if args.no_autodetect:
        config.bitrate_autodetect = False

    result = run_recon(config, mode="slow" if args.deep else "fast")
    print(format_report(result, top_n=args.top))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
        print(f"\n[raw discovery written to {args.json}]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
