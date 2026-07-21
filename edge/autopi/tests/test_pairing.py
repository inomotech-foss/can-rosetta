"""Pairing helper: payload, AP credentials, terminal QR, and the printable block."""

import json

from canrosetta_edge.config import EdgeConfig
from canrosetta_edge.pairing import (
    format_pairing,
    hotspot_credentials,
    interface_ip,
    local_ip,
    pairing_payload,
    pairing_qr_ascii,
)


def _cfg(tmp_path, **kw):
    """EdgeConfig whose hostapd path never exists unless the test writes it.

    Keeps the tests hermetic: a dev machine that happens to run hostapd must
    not leak its real AP credentials into the assertions.
    """
    kw.setdefault("hostapd_conf", str(tmp_path / "hostapd.conf"))
    return EdgeConfig(**kw)


def test_local_ip_is_a_string():
    ip = local_ip()
    assert isinstance(ip, str) and ip.count(".") == 3


def test_interface_ip_unknown_iface_is_none():
    assert interface_ip("no-such-iface0") is None


def test_interface_ip_loopback():
    # Linux CI has "lo"; elsewhere the helper must degrade to None, not raise
    ip = interface_ip("lo")
    assert ip is None or ip.count(".") == 3


def test_payload_has_host_and_token(tmp_path):
    cfg = _cfg(tmp_path, control_port=8765, control_token="abc123")
    p = pairing_payload(cfg, host_ip="192.168.4.1")
    # v1 shape exactly: no "wifi" key when the AP credentials are unknown
    assert p == {"host": "http://192.168.4.1:8765", "token": "abc123"}


def test_hotspot_credentials_from_hostapd(tmp_path):
    conf = tmp_path / "hostapd.conf"
    conf.write_text(
        "# AutoPi Core AP config\n"
        "interface=uap0\n"
        "ssid=AutoPi-1234567890ab\n"
        "wpa_passphrase=  autopi2018  \n"  # values are stripped
    )
    cfg = EdgeConfig(hostapd_conf=str(conf))
    assert hotspot_credentials(cfg) == {"ssid": "AutoPi-1234567890ab", "psk": "autopi2018"}


def test_hotspot_credentials_missing_file_is_none(tmp_path):
    assert hotspot_credentials(_cfg(tmp_path)) is None


def test_hotspot_credentials_missing_keys_is_none(tmp_path):
    conf = tmp_path / "hostapd.conf"
    conf.write_text("interface=uap0\nssid=AutoPi-x\n")  # no wpa_passphrase
    assert hotspot_credentials(EdgeConfig(hostapd_conf=str(conf))) is None


def test_hotspot_credentials_skips_comments(tmp_path):
    conf = tmp_path / "hostapd.conf"
    conf.write_text("#ssid=commented-out\nssid=Real\nwpa_passphrase=pw\n")
    assert hotspot_credentials(EdgeConfig(hostapd_conf=str(conf))) == {
        "ssid": "Real", "psk": "pw",
    }


def test_hotspot_credentials_overrides_win(tmp_path):
    conf = tmp_path / "hostapd.conf"
    conf.write_text("ssid=FileSsid\nwpa_passphrase=filepw\n")
    cfg = EdgeConfig(wifi_ssid="Manual", wifi_psk="manualpw", hostapd_conf=str(conf))
    assert hotspot_credentials(cfg) == {"ssid": "Manual", "psk": "manualpw"}


def test_payload_includes_wifi_when_credentials_known(tmp_path):
    cfg = _cfg(tmp_path, control_port=8765, control_token="t",
               wifi_ssid="AutoPi-abc", wifi_psk="pw")
    p = pairing_payload(cfg, host_ip="192.168.4.1")
    assert p["host"] == "http://192.168.4.1:8765"
    assert p["wifi"] == {"ssid": "AutoPi-abc", "psk": "pw"}


def test_payload_omits_wifi_when_unknown(tmp_path):
    p = pairing_payload(_cfg(tmp_path, control_token="t"), host_ip="192.168.4.1")
    assert "wifi" not in p


def test_qr_ascii_renders():
    art = pairing_qr_ascii(json.dumps({"host": "http://192.168.4.1:8765", "token": "t"}))
    # qrcode is in the [control]/[dev] extras, so this must render (not None)
    assert art is not None and len(art.splitlines()) > 10


def test_format_pairing_includes_host_token_and_qr(tmp_path):
    cfg = _cfg(tmp_path, control_port=8765, control_token="tok-xyz")
    block = format_pairing(cfg, host_ip="10.0.0.5")
    assert "http://10.0.0.5:8765" in block
    assert "tok-xyz" in block
    assert "Pair the phone" in block


def test_format_pairing_shows_wifi_line(tmp_path):
    cfg = _cfg(tmp_path, control_token="tok", wifi_ssid="AutoPi-abc", wifi_psk="pw")
    block = format_pairing(cfg, host_ip="192.168.4.1")
    assert "Wi-Fi: AutoPi-abc" in block
    assert "joins automatically" in block


def test_format_pairing_no_wifi_line_without_credentials(tmp_path):
    block = format_pairing(_cfg(tmp_path, control_token="tok"), host_ip="192.168.4.1")
    assert "Wi-Fi:" not in block


def test_format_pairing_warns_without_token(tmp_path):
    block = format_pairing(_cfg(tmp_path, control_token=""), host_ip="10.0.0.5")
    assert "auth is DISABLED" in block


def test_payload_host_prefers_ap_interface_only_with_credentials(tmp_path, monkeypatch):
    """The advertised host must sit on the network the phone will be on: the AP
    interface when the payload carries the AP credentials (the app joins that
    AP), the routed LAN otherwise (bench/dev pairing over a shared network)."""
    from canrosetta_edge import pairing

    monkeypatch.setattr(pairing, "interface_ip",
                        lambda ifname: "192.168.4.1" if ifname == pairing.AP_INTERFACE else None)
    monkeypatch.setattr(pairing, "local_ip", lambda: "10.0.0.7")

    with_wifi = _cfg(tmp_path, control_token="t", wifi_ssid="AutoPi-abc", wifi_psk="pw12345678")
    assert pairing_payload(with_wifi)["host"] == "http://192.168.4.1:8765"

    without_wifi = _cfg(tmp_path, control_token="t")
    assert pairing_payload(without_wifi)["host"] == "http://10.0.0.7:8765"
