#!/usr/bin/env python3
"""Validate a session directory against the shared JSON Schemas.

Standalone (only needs ``jsonschema``) so CI can check the interoperability seam
without installing any component. Usage:

    python .github/scripts/validate_session.py datasets/sample-session
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema

REPO = Path(__file__).resolve().parents[2]
SCHEMAS = REPO / "schemas"


def load(name: str) -> dict:
    return json.loads((SCHEMAS / name).read_text())


def read_jsonl(path: Path):
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def main(session_dir: str) -> int:
    root = Path(session_dir)

    # 1) every schema is itself a valid Draft 2020-12 schema
    for schema_file in sorted(SCHEMAS.glob("*.json")):
        jsonschema.Draft202012Validator.check_schema(json.loads(schema_file.read_text()))
    print(f"ok: {len(list(SCHEMAS.glob('*.json')))} schemas are valid")

    # 2) the session files conform
    jsonschema.validate(json.loads((root / "manifest.json").read_text()), load("manifest.schema.json"))
    jsonschema.validate(
        json.loads((root / "can" / "discovery.json").read_text()), load("discovery.schema.json")
    )

    checks = [
        ("can/frames.jsonl", "can_frame.record.schema.json"),
        ("phone/motion.jsonl", "motion.record.schema.json"),
        ("phone/location.jsonl", "location.record.schema.json"),
        ("phone/car_hw.jsonl", "car_hw.record.schema.json"),
        ("edge/motion.jsonl", "motion.record.schema.json"),
        ("edge/location.jsonl", "location.record.schema.json"),
        ("labels/telltales.jsonl", "label_telltale.record.schema.json"),
        ("labels/dashboard_ocr.jsonl", "label_dashboard.record.schema.json"),
        ("labels/gear.jsonl", "label_gear.record.schema.json"),
    ]
    for rel, schema_name in checks:
        path = root / rel
        if not path.exists():
            continue
        schema = load(schema_name)
        count = 0
        for rec in read_jsonl(path):
            jsonschema.validate(rec, schema)
            count += 1
        print(f"ok: {count} records in {rel} conform to {schema_name}")

    print(f"session {root} is valid")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
