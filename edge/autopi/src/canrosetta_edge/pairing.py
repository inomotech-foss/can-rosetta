"""Pairing helper for a headless AutoPi.

A headless unit has no screen, so there is nothing to show a QR on. Instead the
edge prints its pairing details — host, control token, and a **terminal ASCII
QR** — over your SSH session (on ``serve`` startup, or via ``canrosetta-edge
pairing``). Scan the QR straight off the terminal, or type the host + token into
the app's manual pairing fields.

The QR encodes the same JSON the app's scanner expects:
``{"host": "http://<ip>:<port>", "token": "<control_token>"}``.
"""

from __future__ import annotations

import json
import socket

from .config import EdgeConfig


def local_ip() -> str:
    """Best-effort primary LAN IPv4 (the address the phone reaches us on)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))  # no packets sent; just picks the route's iface
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"


def pairing_payload(config: EdgeConfig, host_ip: str | None = None) -> dict:
    """The `{host, token}` the phone needs to pair (host defaults to the LAN IP)."""
    ip = host_ip or local_ip()
    return {"host": f"http://{ip}:{config.control_port}", "token": config.control_token}


def pairing_qr_ascii(text: str) -> str | None:
    """Render ``text`` as a scannable ASCII QR, or None if the ``qrcode`` lib is absent."""
    try:
        import io

        import qrcode  # optional; part of the [control] extra
    except ImportError:
        return None
    qr = qrcode.QRCode(border=2)
    qr.add_data(text)
    qr.make(fit=True)
    buf = io.StringIO()
    qr.print_ascii(out=buf, invert=True)
    return buf.getvalue()


def format_pairing(config: EdgeConfig, host_ip: str | None = None) -> str:
    """Human-facing pairing block: host, token, and a QR (or a manual-entry note)."""
    payload = pairing_payload(config, host_ip)
    lines = ["", "── Pair the phone with this AutoPi " + "─" * 24, ""]
    if not config.control_token:
        lines.append("  WARNING: no control_token set — auth is DISABLED (dev only).")
        lines.append("")
    lines += [
        f"  Host:  {payload['host']}",
        f"  Token: {payload['token'] or '(none)'}",
        "",
        "  On the AutoPi Wi-Fi AP the host is usually http://192.168.4.1:"
        f"{config.control_port}.",
        "  In the app: scan the QR below, or enter Host + Token manually.",
        "",
    ]
    qr = pairing_qr_ascii(json.dumps(payload))
    if qr:
        lines.append(qr)
    else:
        lines.append("  (install canrosetta-edge[control] for a scannable QR here;")
        lines.append("   otherwise just type the Host + Token into the app.)")
    lines.append("─" * 60)
    return "\n".join(lines)
