"""Validate that synthesized session files conform to the shared JSON Schemas.

This guards the seam between components: if the schemas and the writer drift, the
whole system stops interoperating. The edge and companion apps validate against
the same schema files.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from canrosetta.synth import generate

jsonschema = pytest.importorskip("jsonschema")

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"


def _load(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text())


def _read_jsonl(path: Path):
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def test_synth_session_matches_schemas(tmp_path):
    root = generate(tmp_path / "sess", duration_s=20.0)

    manifest = json.loads((root / "manifest.json").read_text())
    jsonschema.validate(manifest, _load("manifest.schema.json"))

    discovery = json.loads((root / "can" / "discovery.json").read_text())
    jsonschema.validate(discovery, _load("discovery.schema.json"))

    frame_schema = _load("can_frame.record.schema.json")
    for i, rec in enumerate(_read_jsonl(root / "can" / "frames.jsonl")):
        jsonschema.validate(rec, frame_schema)
        if i > 200:
            break

    motion_schema = _load("motion.record.schema.json")
    for i, rec in enumerate(_read_jsonl(root / "phone" / "motion.jsonl")):
        jsonschema.validate(rec, motion_schema)
        if i > 200:
            break

    loc_schema = _load("location.record.schema.json")
    for rec in _read_jsonl(root / "phone" / "location.jsonl"):
        jsonschema.validate(rec, loc_schema)
