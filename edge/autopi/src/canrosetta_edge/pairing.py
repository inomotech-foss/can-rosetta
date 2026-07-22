"""Pairing helper for a headless AutoPi.

A headless unit has no screen, so there is nothing to show a QR on. Instead the
edge prints its pairing details — host, control token, and a **terminal ASCII
QR** — over your SSH session (on ``serve`` startup, or via ``canrosetta-edge
pairing``). Scan the QR straight off the terminal, or type the host + token into
the app's manual pairing fields.

The QR encodes the same JSON the app's scanner expects (payload v2)::

    {"host": "http://<ip>:<port>", "token": "<control_token>",
     "wifi": {"ssid": "<ap ssid>", "psk": "<ap passphrase>"}}

The ``wifi`` block lets the app join the AutoPi's own Wi-Fi AP programmatically
(no trip to the phone's Settings). It is OMITTED when the credentials are
unknown (dev boxes without an AP) — v1 consumers ignore it, and v2 consumers
must tolerate its absence, so the payload stays backwards compatible.
"""

from __future__ import annotations

import json
import socket

from .config import EdgeConfig

#: The AutoPi's Wi-Fi AP interface. AutoPi Core brings the hotspot up on
#: ``uap0`` (always 192.168.4.1) — the address the phone actually reaches us on.
AP_INTERFACE = "uap0"


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


def interface_ip(ifname: str) -> str | None:
    """The IPv4 address bound to ``ifname``, or None (iface absent/down, non-Linux)."""
    try:
        import fcntl  # Linux-only; guarded so dev on other OSes just falls back
        import struct

        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            packed = struct.pack("256s", ifname[:15].encode())
            return socket.inet_ntoa(
                fcntl.ioctl(s.fileno(), 0x8915, packed)[20:24]  # SIOCGIFADDR
            )
        finally:
            s.close()
    except Exception:  # noqa: BLE001 - best-effort; pairing must never crash on this
        return None


def hotspot_credentials(config: EdgeConfig) -> dict | None:
    """The AP's ``{"ssid", "psk"}`` for the QR payload, or None when unknown.

    Explicit config overrides win (both must be set); otherwise parse the
    hostapd config that AutoPi Core writes for its hotspot. Best-effort by
    design: a missing file or key just means the QR omits the ``wifi`` block
    and the user joins the AP manually, exactly as before payload v2.
    """
    if config.wifi_ssid and config.wifi_psk:
        return {"ssid": config.wifi_ssid, "psk": config.wifi_psk}
    found: dict = {}
    try:
        with open(config.hostapd_conf, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                key, sep, value = line.partition("=")
                if sep and key.strip() in ("ssid", "wpa_passphrase"):
                    found[key.strip()] = value.strip()
    except Exception:  # noqa: BLE001 - never raise; see docstring
        return None
    if "ssid" in found and "wpa_passphrase" in found:
        return {"ssid": found["ssid"], "psk": found["wpa_passphrase"]}
    return None


def pairing_payload(config: EdgeConfig, host_ip: str | None = None) -> dict:
    """The v2 payload the phone needs: ``{host, token}`` + ``wifi`` when known.

    ``host`` must be an address on the network the phone will actually be on.
    When the payload carries the AP credentials the app joins the AP, so the AP
    interface's own address (uap0) is the right one — local_ip() would pick the
    route toward the internet, which on an AutoPi is the LTE modem. Without
    credentials the phone stays on whatever LAN it shares with us (bench/dev
    setups), so keep the routed-LAN address as before.
    """
    wifi = hotspot_credentials(config)
    if wifi is not None:
        ip = host_ip or interface_ip(AP_INTERFACE) or local_ip()
    else:
        ip = host_ip or local_ip()
    payload = {"host": f"http://{ip}:{config.control_port}", "token": config.control_token}
    if wifi is not None:
        payload["wifi"] = wifi  # omitted when unknown; consumers tolerate absence
    return payload


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
    ]
    wifi = payload.get("wifi")
    if wifi:
        lines.append(f"  Wi-Fi: {wifi['ssid']}  (credentials embedded in the QR — "
                     "the app joins automatically)")
    lines += [
        "",
        "  On the AutoPi Wi-Fi AP the host is usually http://192.168.4.1:"
        f"{config.control_port}.",
        "  In the app: scan the QR below, or enter Host + Token manually",
        "  (without the QR, join the AutoPi Wi-Fi manually first).",
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
