"""Minimal HTTP API for CAN-Rosetta signal identification.

This is an *optional* surface: it is only importable when the ``[api]`` extra is
installed (``pip install -e "server/[api]"``), which pulls in FastAPI + uvicorn.
The core library and CLI have no dependency on it, so existing tests keep running
without FastAPI present.

Endpoints:

    GET  /healthz     liveness/readiness probe -> {"status": "ok", ...}
    POST /identify    upload a session archive (.zip / .tar.gz), run the full
                      align->extract->identify pipeline, and return the ranked
                      hypotheses as JSON (the same payload as annotations.json).

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

import tarfile
import tempfile
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse

from . import __version__
from .identify import identify_session
from .session import load_session

app = FastAPI(
    title="CAN-Rosetta Server",
    version=__version__,
    description="Align, extract and identify vehicle CAN signals from a recorded session.",
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
