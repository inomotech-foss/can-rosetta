"""The edge manifest validates against manifest.schema.json."""

import jsonschema

from canrosetta_edge.session import build_manifest, hash_vin, new_session_id


def test_manifest_validates(manifest_schema):
    streams = [
        {"path": "can/frames.parquet", "kind": "can_frames", "rows": 1234,
         "t_start_utc": 1.0, "t_end_utc": 2.0},
        {"path": "can/discovery.json", "kind": "discovery"},
    ]
    manifest = build_manifest(
        new_session_id(),
        device_id="autopi-test",
        vehicle={"make": "VW", "model": "Golf", "year": 2019,
                 "vin_hash": hash_vin("WVWZZZ1KZAW000001")},
        streams=streams,
    )
    jsonschema.validate(manifest, manifest_schema)

    dev = manifest["devices"][0]
    assert dev["role"] == "edge"
    assert dev["kind"] == "autopi"
    assert dev["clock"]["source"] == "ntp"
    assert manifest["schema_version"] == "1.0.0"


def test_vin_is_hashed_not_raw():
    h = hash_vin("WVWZZZ1KZAW000001")
    assert h.startswith("sha256:")
    assert "WVWZZZ1KZAW000001" not in h
