"""Shared test fixtures.

Tests use only the built-in SimulatedTransport and the JSONL fallback writer,
so they need no hardware and none of the optional (can/elm/parquet) extras.
"""

import json
import os

import pytest

REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
SCHEMA_DIR = os.path.join(REPO_ROOT, "schemas")


def load_schema(name: str) -> dict:
    with open(os.path.join(SCHEMA_DIR, name), "r", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="session")
def discovery_schema():
    return load_schema("discovery.schema.json")


@pytest.fixture(scope="session")
def manifest_schema():
    return load_schema("manifest.schema.json")


@pytest.fixture(scope="session")
def can_frame_schema():
    return load_schema("can_frame.record.schema.json")
