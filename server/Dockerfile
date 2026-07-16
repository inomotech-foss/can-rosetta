# syntax=docker/dockerfile:1
#
# Multi-stage build for the CAN-Rosetta server.
#
# Build context is the `server/` directory:
#   docker build -t can-rosetta-server server/
#
# Stage 1 installs the `canrosetta` package plus its runtime deps (numpy,
# pyarrow, FastAPI/uvicorn) into an isolated virtualenv. Stage 2 is a slim,
# non-root runtime that only carries that venv and the source, and runs the API.

ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

RUN python -m venv "$VIRTUAL_ENV"

WORKDIR /build

# Copy only what the build backend (hatchling) needs, so dependency layers cache
# well and the source tree changing does not reinstall the world.
COPY pyproject.toml README.md ./
COPY src ./src

# Install with the API + parquet extras (numpy comes in transitively).
RUN pip install ".[api,parquet]"

FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    CANROSETTA_HOST=0.0.0.0 \
    CANROSETTA_PORT=8000

# Create an unprivileged user to run the service.
RUN groupadd --system --gid 10001 canrosetta \
    && useradd --system --uid 10001 --gid canrosetta --no-create-home --home /nonexistent canrosetta

COPY --from=builder /opt/venv /opt/venv

USER 10001

EXPOSE 8000

# Lightweight liveness check using the packaged Python (no curl in the image).
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)"]

# Runs canrosetta.api:app via uvicorn (see the canrosetta-server console script).
CMD ["canrosetta-server"]
