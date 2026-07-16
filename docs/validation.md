# Design validation

Two different questions, two different test strategies:

- **Does the code work?** → the deterministic, hardware-free tests in CI (unit
  tests + the synthetic end-to-end decode; see [`server/tests`](../server/tests)
  and [`edge/autopi/tests`](../edge/autopi/tests)). These gate every PR.
- **Is the *approach* sound on real vehicles?** → **design validation** against
  public real-world datasets, run offline. This is deliberately *not* in CI
  (datasets are large, need heavyweight readers, and licensing/network make them
  a poor CI dependency).

This document covers the second: what we validate against, and how.

## Datasets

| Dataset | What it gives us | Ground truth | Use |
|---------|------------------|--------------|-----|
| [comma2k19](https://github.com/commaai/comma2k19) (MIT, ~100 GB) | raw CAN **+** IMU **+** GNSS from a real Civic & RAV4 — our exact parallel corpus | signal defs via [opendbc](https://github.com/commaai/opendbc) | end-to-end: decode raw CAN, compare to opendbc |
| [CANdid](https://www.usenix.org/conference/vehiclesec25/presentation/howson) (USENIX VehicleSec'25, Adelaide) | annotated real CAN traffic across vehicles | annotations included | boundary/label accuracy across makes |
| [ReCAN](https://data.mendeley.com/datasets/76knkx3fzv/2) | real CAN + decoded signal series | DBC-derived | matching-quality checks |

## Running it (comma2k19)

comma2k19 is the strongest validator because it contains the *same three
streams this project fuses* — CAN, IMU, GNSS. Convert a segment into a session
and run the normal pipeline:

```bash
# needs the dataset locally + openpilot tools on PYTHONPATH (offline only)
python -c "from canrosetta.ingest import from_comma2k19; \
           from_comma2k19('<segment_dir>', '/data/comma-session')"
canrosetta identify /data/comma-session --out /data/out
```

The importer (`canrosetta.ingest.from_comma2k19`) maps raw CAN → `can/frames`,
the comma device IMU → `edge/motion.jsonl`, and GNSS → sensors, all on one clock
(the comma device, like our AutoPi, sees CAN and its IMU on the same clock — so
the edge-sensor advantage this project relies on holds there too). Any real
`candump -L` capture (CANdid, ReCAN exports, your own drive) goes in via
`canrosetta import-candump`.

Because a **RAM- and disk-limited** box can't hold a 100 GB corpus, validate on a
**single segment at a time** (~1 minute, a few MB of raw CAN) and delete it
before fetching the next.

## Metrics

We adopt the metrics from the ByCAN paper (arXiv:2408.09265) so results are
comparable to the literature:

- **Slicing accuracy** — fraction of a signal's bits our recovered field gets
  right, vs. the opendbc/annotated boundary.
- **Slicing coverage** — fraction of ground-truth signals we locate at all.
- **Labeling accuracy** — fraction we classify correctly (constant / switch /
  counter / checksum / dynamic — see [`taxonomy.py`](../server/src/canrosetta/taxonomy.py)).

For continuous signals we additionally report the correlation and recovered
scale/offset against the opendbc definition (a decoded speed should match
opendbc speed with r≈1 and the right factor).

## What the literature tells us to expect

The published state of the art (ByCAN, CAN-D, READ) tops out around **80%
slicing accuracy / ~95% coverage** and **~69% labeling accuracy** from CAN alone
plus OBD templates. Two of our design choices target exactly the gaps they
report:

- their labeling is limited by OBD-II's small standard PID set → we add a *wider,
  richer* reference corpus (phone + onboard IMU/GNSS + OCR'd dashboard) so more
  signals have something to correlate against;
- their per-vehicle methods start from scratch each car → our foundation model
  pretrains on unlabelled multi-vehicle CAN to transfer across vehicles.

See [methodology.md](methodology.md#prior-work-and-adopted-techniques).
