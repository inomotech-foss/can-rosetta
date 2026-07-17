<h1 align="center">CAN-Rosetta</h1>

<p align="center">
  <b>A foundation model and toolkit for reverse-engineering vehicle CAN buses —
  by grounding the unknown bus in a known parallel corpus of phone sensors and
  dashboard video.</b>
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: Apache-2.0" src="https://img.shields.io/badge/license-Apache--2.0-blue.svg"></a>
  <img alt="status: alpha" src="https://img.shields.io/badge/status-alpha-orange.svg">
</p>

---

Modern vehicles broadcast hundreds of signals on their CAN bus — speed, RPM,
pedal position, individual wheel speeds, battery temperature — but almost none of
it is documented. A handful of signals are readable through the standardized
**OBD-II** and **UDS** diagnostic protocols; the rest is an undocumented stream
of raw bytes that differs per make, model, and model-year.

**CAN-Rosetta decodes that stream.** The trick is right there in the name. Like
the Rosetta Stone decoded Egyptian hieroglyphs by pairing them with a known
Greek text, we decode CAN by recording a *parallel corpus of known-meaning
signals* alongside the raw bus:

- a phone in the cradle logging **GPS speed/heading** and **IMU acceleration**,
- optionally **filming the dashboard** so we can OCR the real gauge readings,
- plus every value we can actively read over **OBD-II / UDS**.

Then we find the bytes in the raw bus that move in lockstep with those known
references. Speed on the GPS and speed on the CAN bus are the *same physical
quantity*; whichever two bytes track GPS speed with r≈1.0 **are** the speed
signal, and linear regression hands you the scale and offset. Repeat across every
reference and you reconstruct the vehicle's signal database.

## The system

Three components, one shared [session data format](docs/data-format.md):

| Component | Where | What it does |
|-----------|-------|--------------|
| [`edge/autopi`](edge/autopi) | in the vehicle (AutoPi) | **discovers** available signals (fast catalog scan + slow brute-force), **continuously logs** every CAN frame, logs its **own IMU/GPS** beside the bus, and serves a **control API** the phone drives |
| [`companion/ios`](companion/ios) | driver's iPhone | logs **IMU + GPS** at high rate, optionally **films the dashboard**, and **remotely steers the AutoPi** (pick mode, start/stop recording) — all UTC-timestamped |
| [`server`](server) | anywhere with a CPU/GPU | **aligns** the clocks, **extracts** bit-field candidates, **identifies** signals against the references, and trains the **foundation model** |

The two in-vehicle devices coordinate peer-to-peer over a local, offline
[control link](docs/control-protocol.md) (the AutoPi serves; the phone drives) —
no internet or server needed in the car. The server processes uploaded sessions
afterward. Real-world `candump -L` logs can be imported straight into the
pipeline with `canrosetta import-candump`.

```
 AutoPi  ──►  can/frames.parquet + can/discovery.json  ─┐
                                                         ├──►  server  ──►  DBC + labels + model
 iPhone  ──►  phone/motion.jsonl + location + video    ─┘
```

See [docs/architecture.md](docs/architecture.md) for the full picture and
[docs/methodology.md](docs/methodology.md) for the five-stage pipeline and the
math behind identification.

## Capabilities

| Area | What it does | Where |
|------|--------------|-------|
| Discovery | fast OBD/UDS catalog scan + slow brute-force sweep + plain-CAN census | `edge/autopi` |
| Logging | continuous CAN capture + AutoPi onboard IMU/GPS (on the CAN clock) | `edge/autopi` |
| Remote control | phone creates a session, picks fast/slow, starts/stops recording; clock-sync handshake | `edge` + `companion` |
| Alignment | estimate the edge↔phone clock offset by cross-correlating redundant signals | `server/align.py` |
| Extraction | enumerate bit-field candidates; drop constants/counters/checksums | `server/extract.py` |
| Identification | correlate candidates vs GPS/IMU/OBD/dashboard refs; fit scale/offset; rank | `server/identify.py` |
| Dashboard perception | OCR digits, telltale on/off, needle angle, gear — video **+ hybrid high-res stills** | `server/perception` |
| EV signals | signed motion refs, regen, battery current/voltage/SoC, V·I & Coulomb priors | `server/ev.py` |
| EV charging | connector state, AC/DC mode, AC voltage/current/phases, charge power | `server/charging.py` |
| Message roles | periodic / sporadic / on-demand classification | `server/roles.py` |
| Command ID | flag **command** signals passively (they *lead* their effect) — never transmits | `server/roles.py` |
| Multiplexing | detect the selector byte (η²) and extract per-selector candidates | `server/mux.py` |
| Export | confident mappings → **DBC**; real logs in via `import-candump` | `server/dbc.py`, `ingest.py` |
| Foundation model | byte tokenizer + fingerprints (numpy) + masked-byte Transformer pretraining (torch) | `server/model` |

## How signals are found

Two complementary layers (details in [methodology](docs/methodology.md)):

1. **Classical baseline (works today, fully unsupervised).** Enumerate every
   plausible bit-field in every periodic frame, align to the reference signals,
   and rank by correlation / mutual information / event-coincidence. Physical
   priors (speed ≥ 0, accel = d(speed)/dt, RPM↔speed via gear ratios)
   disambiguate. Output: ranked hypotheses → a **DBC** file.

2. **Foundation model (the research direction).** Self-supervised pretraining on
   large volumes of *unlabelled* multi-vehicle CAN learns the latent grammar of
   automotive buses (periods, counters, checksums, multiplexing). Fine-tuning on
   the aligned phone/OBD/OCR labels teaches it to recognize signal *types* from
   their behavioral fingerprint — so a brand-new vehicle can be decoded from far
   fewer labelled drives. The baseline generates the labels that train the model;
   the model shrinks the search space the baseline runs over.

## Quick start (server, on synthetic data)

The repo ships a tiny synthetic sample session so you can run the identification
pipeline end-to-end with no hardware:

```bash
cd server
pip install -e ".[dev]"
canrosetta identify ../datasets/sample-session --out /tmp/out
# → prints ranked signal hypotheses and writes /tmp/out/annotations.json + a .dbc
pytest
```

The edge and companion apps have their own build/run instructions in their
respective READMEs.

## ⚠️ Safety & legal

This tool is for use **on vehicles you own or are explicitly authorized to
test**. Discovery is strictly **read-only** — no writes, no actuator/routine
controls, no security-access or programming-session services. Even so, probing a
live bus carries risk. **Read [SAFETY.md](SAFETY.md) before connecting to a
vehicle**, and never run discovery while driving.

## Contributing

Issues and PRs welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). This is an
[inomotech-foss](https://github.com/inomotech-foss) project, licensed under
[Apache-2.0](LICENSE).
