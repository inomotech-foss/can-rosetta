"""Pairing helper: payload, terminal QR, and the printable block (headless setup)."""

import json

from canrosetta_edge.config import EdgeConfig
from canrosetta_edge.pairing import (
    format_pairing,
    local_ip,
    pairing_payload,
    pairing_qr_ascii,
)


def test_local_ip_is_a_string():
    ip = local_ip()
    assert isinstance(ip, str) and ip.count(".") == 3


def test_payload_has_host_and_token():
    cfg = EdgeConfig(control_port=8765, control_token="abc123")
    p = pairing_payload(cfg, host_ip="192.168.4.1")
    assert p == {"host": "http://192.168.4.1:8765", "token": "abc123"}


def test_qr_ascii_renders():
    art = pairing_qr_ascii(json.dumps({"host": "http://192.168.4.1:8765", "token": "t"}))
    # qrcode is in the [control]/[dev] extras, so this must render (not None)
    assert art is not None and len(art.splitlines()) > 10


def test_format_pairing_includes_host_token_and_qr():
    cfg = EdgeConfig(control_port=8765, control_token="tok-xyz")
    block = format_pairing(cfg, host_ip="10.0.0.5")
    assert "http://10.0.0.5:8765" in block
    assert "tok-xyz" in block
    assert "Pair the phone" in block


def test_format_pairing_warns_without_token():
    block = format_pairing(EdgeConfig(control_token=""), host_ip="10.0.0.5")
    assert "auth is DISABLED" in block
