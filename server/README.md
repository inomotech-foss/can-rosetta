# CAN-Rosetta — server

Signal identification and the foundation model. This is the only tier allowed to
be slow: it takes recorded [sessions](../docs/data-format.md) and turns raw CAN
bytes into named, scaled signals — then exports a DBC.

The edge (AutoPi) and companion (iPhone) apps record **fully offline** and never
depend on this server at runtime; sessions are uploaded and processed here after
the fact.

## Install

```bash
cd server
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"     # numpy + pyarrow + pytest + jsonschema + ruff
# optional: the learned model
pip install -e ".[model]"   # adds torch
```

## Try it on synthetic data (no hardware)

```bash
canrosetta make-sample /tmp/demo-session        # write a synthetic drive
canrosetta identify /tmp/demo-session --out /tmp/out
```

`identify` runs the full pipeline and prints, per reference signal, the ranked
CAN bit-field hypotheses — e.g. it recovers that GPS speed lives in
`0x3C0[1:3]` big-endian at scale 0.01 km/h. It writes `annotations.json` and a
`signals.dbc` you can open in SavvyCAN / cantools.

The repo also ships a pre-generated sample at
[`../datasets/sample-session`](../datasets/sample-session) so `canrosetta
identify ../datasets/sample-session` works out of the box.

## Pipeline (see [methodology](../docs/methodology.md))

| Stage | Module | What it does |
|-------|--------|--------------|
| 2 align | `align.py` | estimate the edge↔companion clock offset via cross-correlation |
| 3 extract | `extract.py` | enumerate bit-field candidates per arbitration ID, drop counters/checksums |
| 4 identify | `identify.py` | correlate candidates against references (GPS/IMU/OBD), rank, fit scale/offset |
| 5 model | `model/` | tokenizer + behavioral fingerprints (numpy) and a torch masked-byte pretraining scaffold |
| export | `dbc.py` | write confident mappings as a DBC |

`references.py` builds the "known" signals; `session.py` loads a session;
`signals.py` holds the numpy DSP helpers; `synth.py` generates test/demo data.

## Tests

```bash
pytest            # unit tests + the end-to-end decode-the-synthetic-drive test
ruff check .
```

The E2E test (`tests/test_e2e.py`) generates a drive with known ground truth and
asserts the pipeline recovers speed and RPM and estimates the injected clock
offset. No hardware, no network.
