"""Local HTTP + WebSocket control server for the edge device.

Serves the API in docs/control-protocol.md so the companion phone can steer the
AutoPi: create a session, run discovery in a chosen mode, and start/stop
recording. Peer-to-peer and offline — the AutoPi is the server, the phone is the
client, typically over the AutoPi's own WiFi AP.

aiohttp is imported lazily (``pip install canrosetta-edge[control]``) so the rest
of the package works without it.
"""

from __future__ import annotations

import asyncio
import time

from .engine import Busy, Engine
from .session import SW_VERSION

_UNAUTH_PATHS = {"/api/health"}


def create_app(engine: Engine, token: str = ""):
    """Build the aiohttp application wrapping ``engine``."""
    try:
        from aiohttp import web
    except ImportError as exc:  # noqa: TRY003
        raise ImportError(
            "the control server needs aiohttp; install canrosetta-edge[control]"
        ) from exc

    routes = web.RouteTableDef()

    def _authorized(request) -> bool:
        if not token:
            return True  # dev mode: auth disabled (logged at startup)
        header = request.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            return header[len("Bearer "):] == token
        return request.query.get("token") == token  # for the WS handshake

    @web.middleware
    async def auth_mw(request, handler):
        if request.path in _UNAUTH_PATHS or _authorized(request):
            return await handler(request)
        return web.json_response({"error": "unauthorized"}, status=401)

    @routes.get("/api/health")
    async def health(request):
        return web.json_response({"ok": True, "sw_version": SW_VERSION})

    @routes.get("/api/time")
    async def get_time(request):
        return web.json_response({"t_utc": time.time()})

    @routes.get("/api/status")
    async def status(request):
        return web.json_response(engine.status())

    @routes.post("/api/session")
    async def session(request):
        body = await _json(request)
        try:
            info = engine.start_session(
                session_id=body.get("session_id"),
                vehicle=body.get("vehicle"),
                edge_utc_offset_est_s=body.get("edge_utc_offset_est_s"),
                clock_source=body.get("clock_source"),
            )
        except Busy as b:
            return web.json_response({"error": str(b), "state": b.state}, status=409)
        return web.json_response(info)

    @routes.post("/api/discover")
    async def discover_ep(request):
        body = await _json(request)
        mode = body.get("mode")
        return _start(web, lambda: engine.start_discovery(mode), {"state": "discovering"})

    @routes.post("/api/log/start")
    async def log_start(request):
        body = await _json(request)
        return _start(web, lambda: engine.start_logging(body.get("duration_s")),
                      {"state": "logging"})

    @routes.post("/api/log/stop")
    async def log_stop(request):
        result = await asyncio.get_event_loop().run_in_executor(None, engine.stop)
        return web.json_response(result)

    @routes.post("/api/run")
    async def run_ep(request):
        body = await _json(request)
        mode = body.get("mode")
        return _start(web, lambda: engine.start_run(mode, body.get("duration_s")),
                      {"state": "discovering"})

    @routes.get("/api/discovery")
    async def discovery(request):
        d = engine.read_discovery()
        if d is None:
            return web.json_response({"error": "no discovery yet"}, status=404)
        return web.json_response(d)

    @routes.get("/api/version")
    async def version_ep(request):
        from . import updater
        repo = engine.config.update_repo
        check = request.query.get("check") not in (None, "0", "false")
        # a network check for the latest release can be slow; do it off-loop, opt-in
        if check:
            st = await asyncio.get_event_loop().run_in_executor(
                None, lambda: updater.version_status(repo))
        else:
            st = updater.UpdateStatus(updater.current_version(), None, False, repo)
        return web.json_response({
            "current": st.current, "latest": st.latest,
            "update_available": st.update_available, "repo": st.repo,
            "sw_version": SW_VERSION,
        })

    @routes.post("/api/update")
    async def update_ep(request):
        from . import updater
        if engine.state in ("discovering", "logging"):
            return web.json_response({"error": "busy; stop recording before updating"},
                                     status=409)
        body = await _json(request)
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(None, lambda: updater.update(
                target_tag=body.get("target"),
                repo=engine.config.update_repo,
                allow_remote=engine.config.allow_remote_update,
            ))
        except updater.UpdateError as e:
            return web.json_response({"error": str(e)}, status=400)
        # reply first, then re-exec into the freshly-installed code
        result["restarting"] = True
        loop.call_later(1.0, updater.restart)
        return web.json_response(result)

    @routes.get("/api/ws")
    async def ws_handler(request):
        ws = web.WebSocketResponse(heartbeat=20.0)
        await ws.prepare(request)
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def on_event(event: dict) -> None:
            # engine emits from a worker thread; hop back onto the loop safely
            loop.call_soon_threadsafe(queue.put_nowait, event)

        unsubscribe = engine.subscribe(on_event)
        try:
            await ws.send_json({"event": "status", **engine.status()})
            while not ws.closed:
                event = await queue.get()
                await ws.send_json(event)
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        finally:
            unsubscribe()
        return ws

    app = web.Application(middlewares=[auth_mw])
    app.add_routes(routes)
    return app


async def _json(request) -> dict:
    if not request.can_read_body:
        return {}
    try:
        return await request.json()
    except Exception:
        return {}


def _start(web, action, ok_body: dict):
    """Run a start-job action, mapping engine errors to HTTP status codes."""
    try:
        action()
    except Busy as b:
        return web.json_response({"error": str(b), "state": b.state}, status=409)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    return web.json_response(ok_body, status=202)


def serve(engine: Engine, host: str = "0.0.0.0", port: int = 8765,
          token: str = "") -> None:
    """Run the control server (blocking)."""
    from aiohttp import web

    if not token:
        print("[control] WARNING: no control_token set — auth is DISABLED "
              "(development only). Set control_token in config for real use.")
    app = create_app(engine, token=token)
    print(f"[control] serving on http://{host}:{port}  (session dir: "
          f"{engine.config.output_dir})")
    web.run_app(app, host=host, port=port, print=None)
