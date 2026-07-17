"""Control-server tests using aiohttp's in-process TestClient (no real socket).

Runs against the simulated transport, so the whole HTTP + WebSocket surface is
exercised with no hardware.
"""

import asyncio
import time

import pytest

pytest.importorskip("aiohttp")

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from canrosetta_edge.config import EdgeConfig  # noqa: E402
from canrosetta_edge.control import create_app  # noqa: E402
from canrosetta_edge.engine import Engine  # noqa: E402

TOKEN = "secret-token"


def _make_client(tmp_path):
    cfg = EdgeConfig(transport="simulated", output_dir=str(tmp_path),
                     sensor_source="simulated")
    engine = Engine(cfg, device_id="test-edge")
    app = create_app(engine, token=TOKEN)
    return TestClient(TestServer(app)), engine


def test_health_is_unauthenticated(tmp_path):
    async def run():
        client, _ = _make_client(tmp_path)
        async with client:
            r = await client.get("/api/health")
            assert r.status == 200
            assert (await r.json())["ok"] is True

    asyncio.run(run())


def test_auth_required(tmp_path):
    async def run():
        client, _ = _make_client(tmp_path)
        async with client:
            r = await client.get("/api/status")
            assert r.status == 401
            r = await client.get("/api/status", headers={"Authorization": f"Bearer {TOKEN}"})
            assert r.status == 200

    asyncio.run(run())


def test_session_and_discovery_flow(tmp_path):
    async def run():
        client, engine = _make_client(tmp_path)
        auth = {"Authorization": f"Bearer {TOKEN}"}
        async with client:
            r = await client.post("/api/session", json={"vehicle": {"make": "Sim"}},
                                   headers=auth)
            assert r.status == 200
            session_id = (await r.json())["session_id"]
            assert session_id

            r = await client.post("/api/discover", json={"mode": "fast"}, headers=auth)
            assert r.status == 202

            # poll status until discovery finishes
            deadline = time.monotonic() + 10
            state = None
            while time.monotonic() < deadline:
                st = await (await client.get("/api/status", headers=auth)).json()
                state = st["state"]
                if state == "idle" and st["discovery_summary"]:
                    break
                await asyncio.sleep(0.1)
            assert state == "idle"

            r = await client.get("/api/discovery", headers=auth)
            assert r.status == 200
            assert (await r.json())["obd"]["supported_pids"]

    asyncio.run(run())


def test_discover_requires_mode(tmp_path):
    async def run():
        client, _ = _make_client(tmp_path)
        auth = {"Authorization": f"Bearer {TOKEN}"}
        async with client:
            await client.post("/api/session", json={}, headers=auth)
            r = await client.post("/api/discover", json={"mode": "bogus"}, headers=auth)
            assert r.status == 400

    asyncio.run(run())


def test_websocket_streams_events(tmp_path):
    async def run():
        client, _ = _make_client(tmp_path)
        async with client:
            ws = await client.ws_connect(f"/api/ws?token={TOKEN}")
            first = await asyncio.wait_for(ws.receive_json(), timeout=5)
            assert first["event"] == "status"
            # trigger a state change and confirm an event arrives
            await client.post("/api/session", json={},
                              headers={"Authorization": f"Bearer {TOKEN}"})
            await client.post("/api/discover", json={"mode": "fast"},
                              headers={"Authorization": f"Bearer {TOKEN}"})
            saw_state = False
            for _ in range(20):
                msg = await asyncio.wait_for(ws.receive_json(), timeout=5)
                if msg.get("event") == "state":
                    saw_state = True
                    break
            assert saw_state
            await ws.close()

    asyncio.run(run())


def test_version_endpoint_reports_current(tmp_path):
    async def run():
        client, _ = _make_client(tmp_path)
        auth = {"Authorization": f"Bearer {TOKEN}"}
        async with client:
            r = await client.get("/api/version", headers=auth)  # no network check
            assert r.status == 200
            body = await r.json()
            assert body["current"]
            assert body["repo"] == "inomotech-foss/can-rosetta"
            assert body["update_available"] is False

    asyncio.run(run())
