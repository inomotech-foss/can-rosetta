"""Minimal HTTP API for CAN-Rosetta signal identification.

This is an *optional* surface: it is only importable when the ``[api]`` extra is
installed (``pip install -e "server/[api]"``), which pulls in FastAPI + uvicorn.
The core library and CLI have no dependency on it, so existing tests keep running
without FastAPI present.

Endpoints:

    GET  /                       the web dashboard (server/webui/index.html)
    GET  /webui/*                dashboard static assets (css/js/tokens/fonts)
    GET  /healthz                liveness/readiness probe -> {"status": "ok", ...}
    GET  /api/sessions           list sessions under the sessions root
    GET  /api/sessions/{id}      one session's manifest streams + device/clock info
    GET  /api/sessions/{id}/identify  ranked hypotheses + alignment for a session
    GET  /api/sessions/{id}/census    per-arb-ID roles + detected multiplexors
    POST /identify               upload a session archive (.zip / .tar.gz), run the
                                 full align->extract->identify pipeline, and return
                                 the ranked hypotheses as JSON.

The dashboard reads the ``/api/*`` endpoints and falls back to embedded demo data
when they return nothing, so it renders with or without a sessions corpus.

The sessions root is ``$CANROSETTA_SESSIONS`` when set, else the repository's
``datasets/`` directory (which ships ``datasets/sample-session``).

The uploaded archive is expected to contain a session directory as described in
docs/data-format.md (a ``manifest.json`` and a ``can/`` directory, optionally
``phone/``, ``edge/`` and ``labels/``). It may be wrapped in a single top-level
folder; the server locates the real session root inside the extracted tree.

Run locally::

    uvicorn canrosetta.api:app --host 0.0.0.0 --port 8000

or via the module's helper::

    python -m canrosetta.api
"""

from __future__ import annotations

import json
import os
import tarfile
import tempfile
import zipfile
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .identify import identify_session
from .mux import detect_multiplexor
from .roles import message_roles
from .session import Session, load_session

app = FastAPI(
    title="CAN-Rosetta Server",
    version=__version__,
    description="Align, extract and identify vehicle CAN signals from a recorded session.",
)

# ---------------------------------------------------------------------------
# Static dashboard (server/webui)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _webui_dir() -> Path:
    """Locate the ``webui/`` directory robustly.

    Works both from a source checkout (``server/webui``) and when the package is
    installed (a sibling ``webui`` beside the package is copied in some layouts).
    """
    here = Path(__file__).resolve()
    for base in (here.parent, *here.parents):
        cand = base / "webui"
        if (cand / "index.html").exists():
            return cand
        cand = base / "server" / "webui"
        if (cand / "index.html").exists():
            return cand
    # last resort: the conventional location relative to the package root
    return here.parents[2] / "webui"


_WEBUI = _webui_dir()
if (_WEBUI / "index.html").exists():
    app.mount("/webui", StaticFiles(directory=str(_WEBUI)), name="webui")


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    """Serve the web dashboard shell."""
    index = _WEBUI / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="dashboard not built")
    return FileResponse(str(index), media_type="text/html")


# ---------------------------------------------------------------------------
# Sessions corpus
# ---------------------------------------------------------------------------


def _sessions_root() -> Path:
    """Root directory holding session folders.

    ``$CANROSETTA_SESSIONS`` wins; otherwise the repo's ``datasets/`` directory.
    """
    env = os.environ.get("CANROSETTA_SESSIONS")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for base in here.parents:
        cand = base / "datasets"
        if cand.is_dir():
            return cand
    return here.parents[2] / "datasets"


def _iter_session_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    out = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and (
            (child / "manifest.json").exists() or (child / "can").is_dir()
        ):
            out.append(child)
    return out


def _manifest_of(path: Path) -> dict:
    mf = path / "manifest.json"
    if mf.exists():
        try:
            return json.loads(mf.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _session_id_of(path: Path, manifest: dict) -> str:
    return str(manifest.get("session_id") or path.name)


def _vehicle_label(manifest: dict) -> str:
    v = manifest.get("vehicle") or {}
    parts = [str(v.get(k)) for k in ("make", "model", "year") if v.get(k)]
    return " ".join(parts) if parts else "unknown"


def _resolve_session_dir(session_id: str) -> Path:
    for path in _iter_session_dirs(_sessions_root()):
        if _session_id_of(path, _manifest_of(path)) == session_id:
            return path
    # also allow addressing by directory name
    direct = _sessions_root() / session_id
    if direct.is_dir():
        return direct
    raise HTTPException(status_code=404, detail=f"session {session_id!r} not found")


def _stream_summary(manifest: dict) -> list[dict]:
    out = []
    for s in manifest.get("streams", []):
        out.append(
            {
                "path": s.get("path", ""),
                "kind": s.get("kind", ""),
                "rows": s.get("rows"),
            }
        )
    return out


def _session_brief(path: Path) -> dict:
    manifest = _manifest_of(path)
    streams = manifest.get("streams", [])
    can_rows = next(
        (s.get("rows") for s in streams if s.get("kind") == "can_frames"), None
    )
    return {
        "id": _session_id_of(path, manifest),
        "dir": path.name,
        "vehicle": _vehicle_label(manifest),
        "created_utc": manifest.get("created_utc"),
        "frames": can_rows,
        "streams": len(streams),
        "devices": len(manifest.get("devices", [])),
    }


@app.get("/api/sessions")
def api_sessions() -> JSONResponse:
    """List every session discoverable under the sessions root."""
    sessions = [_session_brief(p) for p in _iter_session_dirs(_sessions_root())]
    return JSONResponse({"sessions": sessions, "root": str(_sessions_root())})


@app.get("/api/sessions/{session_id}")
def api_session_detail(session_id: str) -> JSONResponse:
    """Manifest streams plus device/clock information for one session."""
    path = _resolve_session_dir(session_id)
    manifest = _manifest_of(path)
    return JSONResponse(
        {
            "id": _session_id_of(path, manifest),
            "dir": path.name,
            "vehicle": _vehicle_label(manifest),
            "created_utc": manifest.get("created_utc"),
            "streams": _stream_summary(manifest),
            "devices": manifest.get("devices", []),
            "vehicle_raw": manifest.get("vehicle", {}),
        }
    )


def _load_or_422(path: Path) -> Session:
    try:
        return load_session(path)
    except (ValueError, FileNotFoundError, ImportError) as exc:
        raise HTTPException(status_code=422, detail=f"invalid session: {exc}") from exc


@app.get("/api/sessions/{session_id}/identify")
def api_session_identify(
    session_id: str,
    hz: float = Query(10.0, gt=0, le=1000),
    top_k: int = Query(5, gt=0, le=50),
) -> JSONResponse:
    """Run the identification pipeline and return ranked hypotheses + alignment."""
    path = _resolve_session_dir(session_id)
    session = _load_or_422(path)
    result = identify_session(session, hz=hz, top_k=top_k)
    return JSONResponse(result.as_dict())


@app.get("/api/sessions/{session_id}/census")
def api_session_census(session_id: str) -> JSONResponse:
    """Per-arbitration-ID message roles and any detected multiplexors."""
    path = _resolve_session_dir(session_id)
    session = _load_or_422(path)

    roles = message_roles(session)
    by_id = session.frames.by_id(rx_only=False)
    messages = []
    for aid in sorted(roles):
        role = roles[aid]
        fid = by_id.get(aid)
        dlc = fid.width if fid is not None else 0
        mux = detect_multiplexor(fid) if fid is not None else None
        messages.append(
            {
                "arb_id": aid,
                "arb_id_hex": f"0x{aid:X}",
                "role": role.role,
                "count": role.count,
                "period_ms": role.period_ms,
                "jitter": role.jitter,
                "dlc": dlc,
                "multiplexor": (
                    {
                        "byte_offset": mux.byte_offset,
                        "values": mux.values,
                        "score": mux.score,
                    }
                    if mux is not None
                    else None
                ),
            }
        )
    return JSONResponse(
        {
            "session_id": session.session_id,
            "arbitration_ids": len(messages),
            "frames": int(len(session.frames)),
            "messages": messages,
        }
    )

# Cap uploads to keep the endpoint cheap and DoS-resistant. Sessions are small
# (JSONL / parquet); 256 MiB is generous.
MAX_UPLOAD_BYTES = 256 * 1024 * 1024


@app.get("/healthz")
def healthz() -> dict:
    """Liveness/readiness probe."""
    return {"status": "ok", "service": "canrosetta", "version": __version__}


def _find_session_root(extracted: Path) -> Path:
    """Locate the session directory inside an extracted archive.

    Accepts either the session directory itself at the top level or nested one
    level down inside a single wrapper folder.
    """
    candidates = [extracted, *[p for p in extracted.iterdir() if p.is_dir()]]
    for c in candidates:
        if (c / "manifest.json").exists() or (c / "can").is_dir():
            return c
    raise HTTPException(
        status_code=400,
        detail="archive does not contain a session (need manifest.json or a can/ directory)",
    )


def _safe_extract_zip(archive: Path, dest: Path) -> None:
    with zipfile.ZipFile(archive) as zf:
        for member in zf.namelist():
            target = (dest / member).resolve()
            if not str(target).startswith(str(dest.resolve())):
                raise HTTPException(status_code=400, detail="unsafe path in archive")
        zf.extractall(dest)


def _safe_extract_tar(archive: Path, dest: Path) -> None:
    with tarfile.open(archive) as tf:
        for member in tf.getmembers():
            target = (dest / member.name).resolve()
            if not str(target).startswith(str(dest.resolve())):
                raise HTTPException(status_code=400, detail="unsafe path in archive")
        tf.extractall(dest)


@app.post("/identify")
async def identify(
    session: UploadFile = File(..., description="Session archive (.zip or .tar.gz)"),
    hz: float = Query(10.0, gt=0, le=1000, description="Resample rate for correlation"),
    top_k: int = Query(5, gt=0, le=50, description="Hypotheses to keep per reference"),
    min_r: float = Query(0.9, ge=0, le=1, description="Confidence threshold on |r|"),
) -> JSONResponse:
    """Run the identification pipeline on an uploaded session archive."""
    filename = session.filename or "upload"
    with tempfile.TemporaryDirectory(prefix="canrosetta-") as tmp:
        tmpdir = Path(tmp)
        archive = tmpdir / "upload.bin"
        size = 0
        with archive.open("wb") as fh:
            while chunk := await session.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="upload too large")
                fh.write(chunk)

        extracted = tmpdir / "session"
        extracted.mkdir()
        lower = filename.lower()
        if lower.endswith(".zip") or zipfile.is_zipfile(archive):
            _safe_extract_zip(archive, extracted)
        elif lower.endswith((".tar.gz", ".tgz", ".tar")) or tarfile.is_tarfile(archive):
            _safe_extract_tar(archive, extracted)
        else:
            raise HTTPException(
                status_code=415,
                detail="unsupported archive; upload a .zip or .tar.gz of the session",
            )

        root = _find_session_root(extracted)
        try:
            loaded = load_session(root)
            result = identify_session(loaded, hz=hz, top_k=top_k)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=422, detail=f"invalid session: {exc}") from exc

        payload = result.as_dict()
        payload["confident"] = [h.as_dict() for h in result.confident(min_r=min_r)]
        return JSONResponse(payload)


def main() -> None:
    """Console helper to run the API with uvicorn."""
    import os

    import uvicorn

    uvicorn.run(
        "canrosetta.api:app",
        host=os.environ.get("CANROSETTA_HOST", "0.0.0.0"),  # noqa: S104
        port=int(os.environ.get("CANROSETTA_PORT", "8000")),
    )


if __name__ == "__main__":
    main()
