"""Command-line interface: ``canrosetta``.

    canrosetta make-sample <dir>        generate a synthetic demo session
    canrosetta identify <session>       run the full align->extract->identify pipeline
    canrosetta fingerprint <session>    print per-arbitration-ID behavioral fingerprints

``identify`` is the headline command; run it on the bundled sample to see the
pipeline recover speed and RPM from raw bytes with no hardware.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .dbc import write_dbc
from .identify import identify_session
from .model.fingerprint import fingerprint_frame
from .session import load_session
from .synth import generate


def _cmd_make_sample(args: argparse.Namespace) -> int:
    out = generate(args.dir, duration_s=args.duration, edge_clock_offset_s=args.offset,
                   ev=args.ev)
    print(f"wrote synthetic {'EV ' if args.ev else ''}session to {out}")
    return 0


def _cmd_identify(args: argparse.Namespace) -> int:
    session = load_session(args.session)
    result = identify_session(session, hz=args.hz, top_k=args.top_k)

    a = result.alignment
    print(f"session: {result.session_id}")
    print(
        f"alignment: delta={a.delta:+.3f}s  confidence={a.confidence:.3f}  "
        f"method={a.method}"
    )
    print(f"candidates extracted: {result.n_candidates}\n")

    for ref, hyps in sorted(result.per_reference.items()):
        top = hyps[0]
        print(f"{ref}:")
        for h in hyps:
            mark = " <=" if h is top else "   "
            print(
                f"  {mark} {h.candidate.label:<24} r={h.r:+.3f}  "
                f"y={h.scale:.4g}*x{h.offset:+.4g}  (n={h.n})"
            )
        print()

    confident = result.confident(min_r=args.min_r)
    print(f"confident mappings (|r|>={args.min_r}): {len(confident)}")
    for h in confident:
        print(f"  {h.reference} = {h.candidate.label}  (r={h.r:+.3f})")

    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "annotations.json").write_text(json.dumps(result.as_dict(), indent=2))
        write_dbc(result, str(out / "signals.dbc"), min_r=args.min_r)
        print(f"\nwrote {out / 'annotations.json'} and {out / 'signals.dbc'}")
    return 0


def _cmd_import_candump(args: argparse.Namespace) -> int:
    from .ingest import from_candump

    out = from_candump(args.log, args.out)
    session = load_session(out)
    print(f"imported {out} — {len(session.frames)} frames across "
          f"{len(session.frames.by_id())} arbitration IDs")
    print(f"run: canrosetta identify {out}")
    return 0


def _cmd_perceive(args: argparse.Namespace) -> int:
    from .perception.run import perceive

    counts = perceive(args.session, stride=args.stride)
    if not counts:
        print("no labels produced (check perception.json ROIs and the video)")
    for name, n in sorted(counts.items()):
        print(f"  {name}: {n} samples")
    print("wrote labels/ — now run: canrosetta identify " + args.session)
    return 0


def _cmd_fingerprint(args: argparse.Namespace) -> int:
    session = load_session(args.session)
    for aid, fid in sorted(session.frames.by_id().items()):
        if len(fid.t) < 8:
            continue
        fp = fingerprint_frame(fid)
        print(
            f"0x{aid:X}: period={fp.period_s*1000:.0f}ms n={fp.n_frames} "
            f"counter_byte={fp.counter_byte} checksum_byte={fp.checksum_byte}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="canrosetta", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("make-sample", help="generate a synthetic demo session")
    s.add_argument("dir")
    s.add_argument("--duration", type=float, default=120.0)
    s.add_argument("--offset", type=float, default=0.7, help="edge clock offset (s)")
    s.add_argument("--ev", action="store_true",
                   help="add an EV battery module (voltage/current/SoC)")
    s.set_defaults(func=_cmd_make_sample)

    s = sub.add_parser("identify", help="align + extract + identify signals")
    s.add_argument("session")
    s.add_argument("--out", help="write annotations.json and signals.dbc here")
    s.add_argument("--hz", type=float, default=10.0)
    s.add_argument("--top-k", type=int, default=5)
    s.add_argument("--min-r", type=float, default=0.9)
    s.set_defaults(func=_cmd_identify)

    s = sub.add_parser("fingerprint", help="print behavioral fingerprints per arb ID")
    s.add_argument("session")
    s.set_defaults(func=_cmd_fingerprint)

    s = sub.add_parser("perceive",
                       help="dashboard-video perception -> labels/ (needs video + perception.json)")
    s.add_argument("session")
    s.add_argument("--stride", type=int, default=3, help="use every Nth video frame")
    s.set_defaults(func=_cmd_perceive)

    s = sub.add_parser("import-candump",
                       help="convert a real `candump -L` log into a session")
    s.add_argument("log", help="path to a candump -L log file")
    s.add_argument("out", help="session directory to create")
    s.set_defaults(func=_cmd_import_candump)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv if argv is not None else sys.argv[1:])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
