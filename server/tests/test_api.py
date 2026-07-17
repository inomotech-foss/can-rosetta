"""API + dashboard smoke tests.

These run whenever the ``[dev]`` extra is installed (FastAPI + httpx present),
which is the case in CI. They exercise the real endpoints against the shipped
``datasets/sample-session`` — no mocks, no skips.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from canrosetta.api import app  # noqa: E402

client = TestClient(app)


def _sample_session_id() -> str:
    resp = client.get("/api/sessions")
    assert resp.status_code == 200
    sessions = resp.json()["sessions"]
    assert sessions, "expected at least the sample session"
    # the sample session ships as datasets/sample-session
    for s in sessions:
        if s["dir"] == "sample-session":
            return s["id"]
    return sessions[0]["id"]


def test_dashboard_served():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "canrosetta-server-dashboard" in resp.text


def test_static_assets_served():
    for asset in ("/webui/app.js", "/webui/styles.css", "/webui/tokens.css"):
        resp = client.get(asset)
        assert resp.status_code == 200, asset


def test_healthz_still_works():
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_sessions_lists_sample():
    resp = client.get("/api/sessions")
    assert resp.status_code == 200
    dirs = {s["dir"] for s in resp.json()["sessions"]}
    assert "sample-session" in dirs


def test_session_detail():
    sid = _sample_session_id()
    resp = client.get(f"/api/sessions/{sid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == sid
    assert isinstance(body["streams"], list) and body["streams"]
    assert isinstance(body["devices"], list)


def test_session_identify_has_hypotheses():
    sid = _sample_session_id()
    resp = client.get(f"/api/sessions/{sid}/identify")
    assert resp.status_code == 200
    body = resp.json()
    assert "alignment" in body
    per_ref = body["per_reference"]
    assert per_ref, "expected non-empty per-reference hypotheses"
    # at least one reference has at least one ranked candidate
    assert any(hyps for hyps in per_ref.values())


def test_session_census():
    sid = _sample_session_id()
    resp = client.get(f"/api/sessions/{sid}/census")
    assert resp.status_code == 200
    body = resp.json()
    assert body["arbitration_ids"] >= 1
    assert isinstance(body["messages"], list) and body["messages"]


def test_unknown_session_404():
    resp = client.get("/api/sessions/does-not-exist")
    assert resp.status_code == 404


# --- vision-gap feature endpoints (merge / clusters / coverage / knowledge) ---


def test_coverage_endpoint():
    sid = _sample_session_id()
    resp = client.get(f"/api/sessions/{sid}/coverage")
    assert resp.status_code == 200
    body = resp.json()
    assert body["dynamic_fields"] > 0
    assert 0.0 <= body["coverage"] <= 1.0


def test_clusters_endpoint():
    sid = _sample_session_id()
    resp = client.get(f"/api/sessions/{sid}/clusters")
    assert resp.status_code == 200
    assert "clusters" in resp.json()


def test_merge_status_endpoint():
    resp = client.get("/api/merge-status")
    assert resp.status_code == 200
    assert "sessions" in resp.json()


def test_knowledge_and_confirm_reject(tmp_path, monkeypatch):
    monkeypatch.setenv("CANROSETTA_KB", str(tmp_path / "kb.json"))
    sid = _sample_session_id()

    # reject a hypothesis, then confirm another; the KB summary should reflect it
    r = client.post(f"/api/sessions/{sid}/reject",
                    json={"reference": "obd_coolant_temp", "candidate_label": "0x3C0#bit12"})
    assert r.status_code == 200
    c = client.post(f"/api/sessions/{sid}/confirm",
                    json={"reference": "gps_speed_kmh",
                          "candidate": {"label": "0x3C0[1:3]BEu", "byte_offset": 1}, "r": 0.999})
    assert c.status_code == 200

    k = client.get("/api/knowledge")
    assert k.status_code == 200
    platforms = k.json()["platforms"]
    assert any(p["signals"] >= 1 for p in platforms)
