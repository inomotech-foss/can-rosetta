# Roadmap

Status: **alpha**. The classical identification baseline works end-to-end on
recorded sessions; the learned foundation model is scaffolded, not yet trained.

## Now (works today)

- [x] Shared session data format + JSON Schemas, validated in CI.
- [x] Edge (AutoPi): read-only OBD/UDS discovery (fast catalog + slow brute) and
      continuous CAN logging, offline, with a simulated transport for tests.
- [x] Companion (iPhone): high-rate IMU + GPS + optional dashboard video, offline.
- [x] Server: clock alignment, candidate extraction, correlation-based
      identification, DBC export.
- [x] End-to-end test that decodes a synthetic drive with known ground truth.

## Next

- [x] **Dashboard perception** (`server/canrosetta/perception`): turn filmed
      gauges/lamps into references — digit OCR, telltale on/off, needle angle,
      gear — with **hybrid capture** (video for temporal density + periodic
      high-res stills for OCR accuracy).
- [x] **Event references**: telltale/gear detection identifies discrete/flag
      signals, not just continuous ones.
- [x] **Session merging** (`merge.py`): stitch edge + companion parts sharing a
      `session_id`; report "awaiting merge" when a counterpart is missing.
- [x] **Unidentified-signal clustering** (`clusters.py`): surface structured
      signals that match no reference, grouped by mutual correlation (active-
      learning targets).
- [x] **Cross-vehicle knowledge base** (`kb.py`): persistent confirmed mappings +
      per-platform **rejection memory** (false friends stay rejected), a
      **coverage** metric, and confirm→`annotations.json`.
- [x] **Sync-marker alignment** (`align.estimate_from_markers`): pin clocks off a
      deliberate marker (e.g. brake_pulse) as a cross-check to cross-correlation.
- [ ] **Cross-component integration test**: drive the edge simulator and a
      synthetic phone part into one session and run the server on it.
- [x] **Multiplexed signal handling** (`mux.py`): detect the selector byte via
      correlation ratio (η²) and extract candidates per selector value.
- [ ] **Phone→vehicle frame estimation** so IMU axes become true longitudinal /
      lateral acceleration references.
- [x] **EV-specific signals**: signed motion references + regen event + EV OBD
      SoC + battery-power/Coulomb priors identify pack current/voltage/SoC
      (`ev.py`). Still to do: per-OEM UDS DID catalogs, cell-level signals.
- [x] **Command-vs-status message classification** (passive causality analysis):
      `roles.py` classifies message cadence and flags command signals that *lead*
      their effect. Strictly read-only (never transmits).
- [ ] **EV charging** connector/mode/AC-metering: added; still to do — cross-session
      de-collinearization of the charging-power group.
- [x] **Car projection** ([design](car-projection.md)): Android Auto IoT car
      app — head-unit status + coordinated start/stop — plus `CarHardwareManager`
      reference logging to `phone/car_hw.jsonl` (every fetch logged, including
      `unavailable`: per-OEM availability evidence); iOS interactive widget +
      Live Activity (on the CarPlay Dashboard from iOS 26, no entitlement);
      `produced_by_accessory` GPS provenance on iOS location records.
- [ ] **MBUX spike**: open the car app on the eVito once per drive and read the
      `car_hw.jsonl` statuses — does MBUX forward anything? No Mercedes data
      point exists yet.
- [ ] **CarPlay full app**: blocked on the `carplay-driving-task` entitlement
      (application pending, owned by the developer account).
- [ ] **Cloud reference fallback**: Mercedes Fleet API / Smartcar / Enode —
      minutes-cadence odometer/SoC anchors if MBUX forwards nothing.

## Foundation model

- [ ] Data pipeline to assemble large multi-vehicle pretraining corpora from raw
      CAN logs (no labels needed).
- [ ] Train the masked-byte encoder (`server/canrosetta/model/pretrain.py`) and
      publish a checkpoint.
- [ ] Fine-tune a signal-type head on aligned labels from the baseline; measure
      how many labelled drives a new vehicle needs.
- [ ] Feed model predictions back to shrink the baseline's candidate search.

## Nice to have

- [ ] Session upload/merge service and a small web UI to review hypotheses.
- [ ] Confidence calibration and active-learning prompts ("drive with the turn
      signal on to disambiguate 0x3C0 bit 4").
- [ ] Export to formats beyond DBC (ARXML, KCD).

Contributions to any of these are welcome — see [CONTRIBUTING.md](../CONTRIBUTING.md).
