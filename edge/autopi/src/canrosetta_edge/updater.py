"""Phone-driven over-the-air update of the edge app.

Provisioning an AutoPi is otherwise fiddly: SSH in, pip install, wire up a
service. Instead we use a **bootstrap-once, then OTA** model — a one-time
installer (scripts/bootstrap.sh) puts this always-on control service on the
device; after that the phone updates it over the local control link via
``POST /api/update``. The AutoPi pulls the requested release of *this same
package* straight from the official GitHub repo and re-execs into it.

Safety: updates only ever install ``canrosetta-edge`` from the **official repo**
(``inomotech-foss/can-rosetta``) over HTTPS at a pinned ``edge-v*`` tag — a
non-official source is refused. This changes only the edge *software*; it has no
bearing on the read-only-vehicle guarantee in SAFETY.md.

The network fetch and the pip invocation are injectable so the guard/version
logic is unit-tested without touching the network.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

OFFICIAL_REPO = "inomotech-foss/can-rosetta"
_TAG_PREFIX = "edge-v"


def current_version() -> str:
    try:
        from importlib.metadata import version
        return version("canrosetta-edge")
    except Exception:  # noqa: BLE001 - fall back to the baked-in constant
        from . import __version__
        return __version__


def parse_version(v: str) -> tuple[int, ...]:
    """Parse ``1.2.3`` (or an ``edge-v1.2.3`` tag) into a comparable tuple."""
    v = v.strip()
    if v.startswith(_TAG_PREFIX):
        v = v[len(_TAG_PREFIX):]
    v = v.lstrip("v").split("+")[0].split("-")[0]
    parts = []
    for p in v.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts) or (0,)


def is_newer(candidate: str, than: str) -> bool:
    return parse_version(candidate) > parse_version(than)


def is_official(repo: str) -> bool:
    return repo == OFFICIAL_REPO


def _pip_spec(repo: str, tag: str) -> str:
    """The pip requirement that installs the edge package at ``tag`` from ``repo``."""
    return (f"canrosetta-edge[control] @ "
            f"git+https://github.com/{repo}@{tag}#subdirectory=edge/autopi")


def _default_fetch_latest(repo: str, timeout: float = 8.0) -> dict | None:
    """Best-effort: latest ``edge-v*`` release tag from the GitHub API (stdlib only)."""
    url = f"https://api.github.com/repos/{repo}/releases?per_page=30"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - https only
            releases = json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None
    tags = [r.get("tag_name", "") for r in releases if str(r.get("tag_name", "")).startswith(_TAG_PREFIX)]
    if not tags:
        return None
    latest = max(tags, key=parse_version)
    return {"tag": latest, "version": latest[len(_TAG_PREFIX):]}


@dataclass
class UpdateStatus:
    current: str
    latest: str | None
    update_available: bool
    repo: str


def version_status(repo: str = OFFICIAL_REPO, *,
                   fetch_latest: Callable[[str], dict | None] | None = None) -> UpdateStatus:
    cur = current_version()
    latest = (fetch_latest or _default_fetch_latest)(repo)
    lv = latest["version"] if latest else None
    return UpdateStatus(cur, lv, bool(lv and is_newer(lv, cur)), repo)


class UpdateError(RuntimeError):
    pass


def update(target_tag: str | None = None, *, repo: str = OFFICIAL_REPO,
           allow_remote: bool = True,
           fetch_latest: Callable[[str], dict | None] | None = None,
           runner: Callable[[list[str]], subprocess.CompletedProcess] | None = None) -> dict:
    """Install the edge package at ``target_tag`` (latest if None) from ``repo``.

    Refuses a non-official repo or when remote updates are disabled. Returns a
    dict with the from/to versions; the caller triggers the restart.
    """
    if not allow_remote:
        raise UpdateError("remote updates are disabled (set allow_remote_update: true)")
    if not is_official(repo):
        raise UpdateError(f"refusing to update from a non-official source: {repo!r}")

    if target_tag is None:
        latest = (fetch_latest or _default_fetch_latest)(repo)
        if not latest:
            raise UpdateError("could not determine the latest release (offline?)")
        target_tag = latest["tag"]
    if not target_tag.startswith(_TAG_PREFIX):
        raise UpdateError(f"target must be an {_TAG_PREFIX}* tag, got {target_tag!r}")

    before = current_version()
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", _pip_spec(repo, target_tag)]
    run = runner or (lambda c: subprocess.run(c, capture_output=True, text=True, timeout=600))
    proc = run(cmd)
    if proc.returncode != 0:
        raise UpdateError(f"pip install failed: {(proc.stderr or proc.stdout or '').strip()[-500:]}")
    return {"ok": True, "from": before, "to": target_tag[len(_TAG_PREFIX):], "repo": repo}


def restart() -> None:  # pragma: no cover - replaces the process
    """Re-exec the current process so the freshly-installed code takes effect.

    Under the bootstrap systemd unit the service is Restart=always, so even a
    plain exit relaunches; execv makes the reload immediate.
    """
    os.execv(sys.executable, [sys.executable, *sys.argv])
