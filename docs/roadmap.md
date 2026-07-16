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

- [ ] **Cross-component integration test**: drive the edge simulator and a
      synthetic phone part into one session and run the server on it.
- [ ] **Dashboard OCR** to turn filmed gauges into labelled references (fuel
      level, gear, warning lamps the OBD layer never exposes).
- [ ] **Event references**: brake/turn-signal/gear detection from IMU + video to
      identify discrete/flag signals, not just continuous ones.
- [ ] **Multiplexed signal handling** in extraction (multiplexed CAN frames).
- [ ] **Phone→vehicle frame estimation** so IMU axes become true longitudinal /
      lateral acceleration references.

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
