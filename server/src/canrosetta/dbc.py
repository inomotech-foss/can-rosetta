"""Export confident hypotheses as a DBC file.

DBC is the de-facto interchange format for CAN signal databases; emitting one
means the results drop straight into SavvyCAN, python-can, cantools, Vector
tools, etc. We only emit signals we're confident about (see
``IdentifyResult.confident``).

Bit numbering follows the usual DBC conventions:
- little-endian (Intel, ``@1``): start bit is the LSB at ``byte_offset*8``;
- big-endian (Motorola, ``@0``): start bit is the MSB at ``byte_offset*8 + 7``.
"""

from __future__ import annotations

import re

from .identify import Hypothesis, IdentifyResult


def _sig_name(ref: str) -> str:
    name = re.sub(r"[^0-9A-Za-z_]", "_", ref)
    return name if name[:1].isalpha() or name[:1] == "_" else f"s_{name}"


def _signal_line(h: Hypothesis) -> str:
    c = h.candidate
    if c.width_bytes == 0:  # single bit
        start, length, order, sign = c.bit_index, 1, 1, "+"
    elif c.endian == "little":
        start, length = c.byte_offset * 8, c.width_bytes * 8
        order, sign = 1, "-" if c.signed else "+"
    else:  # big endian / Motorola
        start, length = c.byte_offset * 8 + 7, c.width_bytes * 8
        order, sign = 0, "-" if c.signed else "+"

    unit = ""  # unit lives on the reference, not the raw field; left blank here
    return (
        f' SG_ {_sig_name(h.reference)} : {start}|{length}@{order}{sign} '
        f"({h.scale:g},{h.offset:g}) [0|0] \"{unit}\" Vector__XXX"
    )


def to_dbc(result: IdentifyResult, *, min_r: float = 0.9, node: str = "ROSETTA") -> str:
    """Render confident hypotheses to DBC text.

    Signals mapped to the same arbitration ID are grouped into one ``BO_``
    message. A comment records the correlation each mapping was accepted on.
    """
    confident = result.confident(min_r=min_r)
    by_msg: dict[int, list[Hypothesis]] = {}
    for h in confident:
        by_msg.setdefault(h.candidate.arb_id, []).append(h)

    lines = ['VERSION "canrosetta"', "", "NS_ :", "", "BS_:", "", f"BU_: {node}", ""]
    for arb_id, hyps in sorted(by_msg.items()):
        max_byte = max(h.candidate.byte_offset + max(h.candidate.width_bytes, 1) for h in hyps)
        dlc = min(max(max_byte, 8), 64)
        lines.append(f"BO_ {arb_id} MSG_{arb_id:X}: {dlc} {node}")
        for h in hyps:
            lines.append(_signal_line(h))
        lines.append("")

    comments = [
        f'CM_ BO_ {h.candidate.arb_id} '
        f'"{_sig_name(h.reference)} matched r={h.r:.3f} (candidate {h.candidate.label})";'
        for h in confident
    ]
    return "\n".join(lines + comments) + "\n"


def write_dbc(result: IdentifyResult, path: str, *, min_r: float = 0.9) -> None:
    from pathlib import Path

    Path(path).write_text(to_dbc(result, min_r=min_r), encoding="utf-8")
