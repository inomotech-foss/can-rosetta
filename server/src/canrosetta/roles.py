"""Classify message *role* and identify *command* signals — passively.

Beyond "what does this signal mean" there's "what does this message *do*": is it a
periodic status broadcast, an event-triggered message, or a diagnostic response?
And which signals are **commands** (an ECU telling another to act) versus status
(reporting a measured state)?

We answer both by observation only — **no frames are ever transmitted** (see
SAFETY.md). Roles come from timing statistics. Commands come from *causality*: a
command's change **precedes** its effect (the actuator response, a status change,
or a physical reference). A signal whose transitions consistently *lead* an effect
by a positive lag, with high correlation, is a command candidate for that effect.

This identifies command *structure* from recorded traffic — the same thing a DBC's
TX definitions capture. It does not, and this project will not, synthesize or
inject commands onto a vehicle bus.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .extract import extract_session
from .session import Session, TimeSeries
from .signals import best_lag, common_grid, resample_uniform

# roles
PERIODIC = "periodic"  # regular cadence -> status broadcast
SPORADIC = "sporadic"  # irregular / bursty -> event or command
ON_DEMAND = "on_demand"  # only appears in response to a request -> diagnostic


@dataclass
class MessageRole:
    arb_id: int
    role: str
    count: int
    period_ms: float
    jitter: float  # coefficient of variation of inter-frame intervals


def message_roles(session: Session, *, cv_periodic: float = 0.25) -> dict[int, MessageRole]:
    """Classify every arbitration ID by transmission cadence.

    ``jitter`` is the interval CV (std/mean). Low CV ⇒ a fixed-rate periodic
    broadcast (status); high CV ⇒ sporadic (event-triggered/command). Frames that
    only appear as induced diagnostic responses are marked on-demand.
    """
    out: dict[int, MessageRole] = {}
    for aid, fid in session.frames.by_id(rx_only=False).items():
        t = np.sort(fid.t_mono)
        n = len(t)
        if n < 3:
            role, period_ms, cv = ON_DEMAND, 0.0, 0.0
        else:
            d = np.diff(t)
            mean = float(np.mean(d))
            cv = float(np.std(d) / mean) if mean > 0 else 1.0
            period_ms = mean * 1000.0
            role = PERIODIC if cv <= cv_periodic else SPORADIC
        out[aid] = MessageRole(aid, role, n, period_ms, cv)
    return out


def causality_lead(cause: TimeSeries, effect: TimeSeries, *, hz: float = 20.0,
                   max_lead_s: float = 1.5, delta: float = 0.0) -> tuple[float, float]:
    """Estimate how far ``cause`` *leads* ``effect`` (seconds) and the correlation.

    Positive lead ⇒ cause precedes effect (the command signature). ``delta`` shifts
    the cause onto the effect's clock (edge→companion) when they differ. Uses the
    same lag search as alignment, on a shared uniform grid.
    """
    cause_t = cause.t + (delta if cause.clock == "edge" else 0.0)
    eff_t = effect.t + (delta if effect.clock == "edge" else 0.0)
    grid = common_grid(cause_t, eff_t, hz=hz)
    if len(grid) < 16:
        return 0.0, 0.0
    c = resample_uniform(cause_t, cause.v, grid, max_gap=1.0)
    e = resample_uniform(eff_t, effect.v, grid, max_gap=1.0)
    # best_lag(ref=effect, sig=cause): L>0 means cause(t) ~ effect(t+L), i.e. cause leads
    lead, r = best_lag(e, c, hz=hz, max_lag_s=max_lead_s)
    return lead, r


@dataclass
class CommandHypothesis:
    effect: str
    arb_id: int
    candidate_label: str
    lead_s: float
    r: float


def command_candidates(session: Session, effects: list[TimeSeries], *,
                       delta: float = 0.0, min_lead_s: float = 0.08,
                       min_r: float = 0.6, top_k: int = 3) -> dict[str, list[CommandHypothesis]]:
    """Rank candidate command signals for each effect by how much they lead it.

    ``effects`` are reference/status series the command would drive (e.g. a
    deceleration, a lamp state). For each effect, extract-derived candidates that
    lead it by ``> min_lead_s`` with ``|r| > min_r`` are returned, best lead first.
    """
    candidates = extract_session(session.frames.by_id(rx_only=True))
    out: dict[str, list[CommandHypothesis]] = {}
    for eff in effects:
        scored: list[CommandHypothesis] = []
        for cand, ts in candidates:
            lead, r = causality_lead(ts, eff, delta=delta)
            if lead > min_lead_s and abs(r) >= min_r:
                scored.append(CommandHypothesis(eff.name, cand.arb_id, cand.label, lead, r))
        scored.sort(key=lambda h: (abs(h.r), h.lead_s), reverse=True)
        if scored:
            out[eff.name] = scored[:top_k]
    return out
