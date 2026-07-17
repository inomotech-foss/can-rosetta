# Methodology

CAN-Rosetta turns an opaque vehicle bus into a labelled catalog of signals. The
core insight is a **Rosetta-stone strategy**: we don't guess what CAN bytes mean
in a vacuum. We record a *parallel corpus* of known-meaning reference signals —
GPS speed, phone IMU, OCR'd dashboard readings, and any OBD/UDS values we can
actively read — and then find the bytes in the unknown plain-CAN stream that move
in lockstep with them.

The pipeline has five stages. Stages 1a/1b run **in the vehicle** (edge);
stages 2–5 run **on the server**.

```
   ┌── in vehicle (AutoPi) ──┐        ┌────────── server ──────────┐
   1a Discover  1b Log    +   phone   2 Align  3 Extract  4 Identify  5 Model
   what's there  raw CAN     sensors    clocks   candidates   signals    (foundation)
```

## Stage 1a — Discovery (edge)

Before/while logging, enumerate what the vehicle actually answers. Two strategies,
run in order of increasing cost and intrusiveness:

**Fast — catalog scan.** Query the standard, well-known request tables:
- **OBD-II** mode 01: read the *supported-PID* bitmasks (PID `0x00`, `0x20`,
  `0x40`, …) to learn which of the standardized PIDs the vehicle exposes, then
  read those. These are public and identical across manufacturers.
- **UDS** ReadDataByIdentifier: try the standardized DID ranges (e.g. `0xF190`
  VIN, `0xF18C` serial) against the usual ECU addresses.

This is fast (seconds to a couple of minutes) and non-destructive. It yields a
set of *labelled, decoded* signals for free.

**Slow — brute-force sweep.** For coverage beyond the catalog:
- sweep OBD PIDs `0x00–0xFF` across all modes of interest;
- sweep UDS DIDs `0x0000–0xFFFF` per responding ECU (this is large — throttled
  and resumable);
- passively census *plain* CAN: which arbitration IDs appear, at what period,
  and which byte positions ever change.

Only **safe, read-style services** are ever issued (see
[SAFETY.md](../SAFETY.md)). We never write, never issue routine/actuator
controls, never touch security-access or session-control services that could put
an ECU into a programming state. Discovery output is `can/discovery.json`.

## Stage 1b — Continuous logging (edge)

Simultaneously, sniff and record **every** frame on the bus to
`can/frames.parquet`, tagged with a monotonic timestamp. This is the haystack.
Meanwhile the OBD/UDS pollers keep sampling the discovered signals at a steady
rate so we have a dense, timestamped reference series, not just one reading.

## Stage 2 — Time alignment (server)

Two clocks, one physical world. We align in two steps:

1. **Coarse** — trust `t_utc` and the manifest clock-sync priors to get within
   ~1 s.
2. **Fine** — pick a pair of physically-redundant series that both clocks
   observe and maximize their cross-correlation over a small lag window:
   - OBD `vehicle_speed` vs GPS ground speed (both are speed);
   - a candidate CAN byte vs phone longitudinal acceleration (derivative of
     speed);
   - filmed brake-light flashes vs CAN brake frames vs IMU deceleration.

   The lag that maximizes correlation is the residual clock offset; apply it.
   Report the achieved alignment error back into the session.

Resample everything onto a common time grid *after* alignment, never before.

## Stage 3 — Candidate extraction (server)

The plain-CAN stream is a set of periodic frames, each a bag of bits. We turn
each `(arb_id)` into a set of **candidate signals** by enumerating plausible
bit-field interpretations:

- group consecutive bytes (1, 2, 3, 4 bytes) at every offset;
- both byte orders (big/little endian);
- signed and unsigned;
- also enumerate individual bits and bit-runs for flags/counters;
- discard fields that are constant, or that look like counters/CRCs (monotonic
  rollover, or a byte that changes every frame with high entropy — classic
  message-counter / checksum tells).

Each candidate becomes a time series `value(arb_id, offset, width, endian,
signed)(t)`. A single 8-byte frame yields a few dozen candidates; a bus yields
tens of thousands. This is embarrassingly parallel and cache-friendly.

## Stage 4 — Signal identification (server)

Now match candidates to references. For each (candidate, reference) pair, after
alignment and resampling, score the relationship:

- **Correlation / mutual information** for continuous references (speed, RPM,
  accel). A candidate that is an affine transform of GPS speed *is* the speed
  signal; linear regression recovers the scale/offset (the DBC factor & offset).
- **Change-point / event coincidence** for discrete references (brake pressed,
  turn signal, gear change from OCR): does a bit flip exactly when the event
  occurs?
- **Physical consistency** as a prior: speed is non-negative and rate-limited;
  RPM and speed are correlated through gear ratios; longitudinal accel is the
  derivative of speed. These constrain and disambiguate.

The output is ranked hypotheses per reference: "GPS speed is best explained by
`arb_id 0x3C0`, bytes 1–2, big-endian, unsigned, ×0.01 km/h, r=0.998." High-
confidence hypotheses are written to `labels/annotations.json` and can be
exported as a **DBC** file.

## Stage 5 — The foundation model

Stages 3–4 are a strong, fully-unsupervised **classical baseline** that already
produces useful DBCs. The foundation-model layer generalizes across vehicles:

- **Self-supervised pretraining** on large volumes of raw multi-vehicle CAN
  (masked-frame / next-frame prediction over byte and bit tokens) learns the
  latent structure of automotive buses — periodicities, counters, checksums,
  multiplexing — without labels.
- **Reference-grounded fine-tuning** uses the aligned phone/OBD/OCR labels from
  many sessions to learn a mapping from a candidate's *behavioral fingerprint*
  (its statistics, its correlation with physical priors, its position in a frame)
  to a *signal type* — so on a brand-new vehicle the model can propose "this is
  probably wheel-speed" before any GPS correlation, and correlation just
  confirms it.
- **Transfer** is the payoff: labels are expensive (they need a drive with a
  phone), raw CAN is cheap. A model pretrained on the cheap data needs far fewer
  labelled drives to decode the next vehicle.

The baseline and the model are complementary: the baseline generates the labels
that train the model, and the model shrinks the search space the baseline runs
over. See [`server/README.md`](../server/README.md) for the concrete package
layout and how the baseline is wired today versus where the learned model plugs
in.

## Prior work and adopted techniques

CAN reverse-engineering has a solid literature; we build on it rather than
reinventing it. Techniques adopted:

- **Byte→bit boundary detection and a behavioral taxonomy** from **ByCAN**
  (Zhou et al., arXiv:2408.09265). ByCAN clusters fields at byte granularity then
  refines at bit granularity, and labels each field *Unused / Switch / Dynamic /
  Verification* using flip-rate × distinct-value ratio. We implement the taxonomy
  in [`taxonomy.py`](../server/src/canrosetta/taxonomy.py) (constant / switch /
  counter / checksum / dynamic) and use it to pre-filter candidates — only
  *dynamic* fields are worth correlating; constants, counters and checksums are
  dropped up front (Stage 3 already discards the latter two).
- **Dynamic Time Warping** for matching series with mismatched sampling rates —
  a slow OBD/GPS reference (1–10 Hz) against a fast CAN broadcast (≥50 Hz) — also
  from ByCAN. Implemented as a **banded** (Sakoe-Chiba) DTW in
  [`signals.py`](../server/src/canrosetta/signals.py) so it stays O(n) in memory.
  It complements the cross-correlation matcher: correlation finds the global lag
  cheaply; DTW is the robust fallback when timing drifts nonlinearly.
- **opendbc as ground truth** (comma.ai): the public DBCs let us score recovered
  signals against real definitions.

## Electric vehicles

EVs put a distinctive signal family on the bus — HV **battery** voltage/current,
**state of charge**, **cell** voltages/temps, **motor** speed/torque, and
**regenerative braking** — handled in [`ev.py`](../server/src/canrosetta/ev.py):

- **Signed longitudinal acceleration** is the key reference: motor torque and
  battery current are *signed* (positive under drive, **negative under regen**),
  so `imu_accel_long` distinguishes them from `|accel|`. A CAN field that tracks
  signed accel and dips below zero on deceleration is the current/torque signal —
  and the sign is why its *signed* interpretation wins over unsigned.
- **Regen event**: deceleration that isn't the friction brake, derived as a
  reference the current/torque sign should follow.
- **Physical priors**: battery power = V·I (links three candidates), and SoC is
  the Coulomb integral of current (`soc_from_current` cross-checks a candidate SoC
  against a candidate current). SoC is also readable directly via OBD PID `0x5B`.
- Edge discovery reads PID `0x5B` in the fast scan; deeper EV battery data is
  manufacturer-specific UDS (`0x22`), reached by the brute-force DID sweep.

### Charging

Charging is a separate regime ([`charging.py`](../server/src/canrosetta/charging.py)):
the car is parked and plugged in, so GPS/IMU are flat. Charging signals —
connector/charge **state** (idle → connected → locked → charging → complete),
AC-vs-DC **mode**, **AC voltage/current/phase-count**, **power**, and the
charge-active flag — are grounded instead by:

- the car's charge screen / a charge telltale (via perception) as the state and
  active-flag references;
- the **EVSE/charger display** OCR'd for AC voltage/current/power;
- **rising SoC** during the charge;
- **physics**: AC power = phases·V·I·pf equals DC pack power V·I (minus losses).

An honest caveat this project surfaces: in a **single** charging session every
power-proportional signal (AC current, DC current, power, the charge-active flag)
switches on together and is therefore **collinear** — the identifier resolves the
charging-signal *group* but can't separate AC-current from DC-current from that
one session alone. What *is* uniquely identifiable is the multi-level charge
**state** (distinctive shape) and the rising **SoC**. Separating the collinear
group needs either multiple sessions with different power levels, or the AC/DC
power identity plus the phase count and nominal voltage — which is exactly why
we capture phase count and AC voltage even though they're near-constant within a
session. `classify_mode` and `infer_phase_count` implement the AC/DC and
phase-count discrimination.

Where we go beyond them: ByCAN and peers (CAN-D, READ) decode from CAN + the
standard OBD-II PID set alone. We ground the bus in a *much richer* reference
corpus — phone and **onboard (edge-clock) IMU/GNSS**, plus OCR'd dashboard video
— which reaches signals no OBD PID exposes, and we add a cross-vehicle foundation
model. Validation against real datasets (comma2k19, CANdid, ReCAN) is described
in [validation.md](validation.md).
