"""Recon / addressing / multi-responder discovery -- all hardware-free.

Uses a fake transport that implements ``request_all`` (the multi-responder API)
in-process, so the 11-bit + 29-bit discovery path is exercised without a real
SocketCAN bus.
"""

import jsonschema

from canrosetta_edge.config import EdgeConfig
from canrosetta_edge.discovery import _addressing_profiles, discover, plain_can_census
from canrosetta_edge.recon import format_report
from canrosetta_edge.transport import (
    OBD_FUNCTIONAL_TX,
    OBD_FUNCTIONAL_TX_29,
    OBD_RESP_BASE,
    Frame,
    diag_response_filter,
    fc_target_for,
    is_diag_response_id,
    phys_req_29,
    phys_resp_29,
    req_id_for_response,
    single_id_filter,
)


# --------------------------------------------------------------------------- #
# Addressing math
# --------------------------------------------------------------------------- #
def test_29bit_addressing_helpers():
    assert phys_req_29(0x10) == 0x18DA10F1
    assert phys_resp_29(0x10) == 0x18DAF110
    # request id for a responder is the reply-to / flow-control target
    assert req_id_for_response(0x18DAF110, True) == 0x18DA10F1
    assert fc_target_for(0x7E8, False) == 0x7E0
    assert fc_target_for(OBD_RESP_BASE + 3, False) == 0x7E3


def test_is_diag_response_id_rejects_broadcast():
    # legal diagnostic responses
    assert is_diag_response_id(0x7E8, False)
    assert is_diag_response_id(0x7EF, False)
    assert is_diag_response_id(0x18DAF110, True)
    # ordinary broadcast ids must NOT be mistaken for responses
    assert not is_diag_response_id(0x003, False)
    assert not is_diag_response_id(0x75E, False)
    assert not is_diag_response_id(0x18DB33F1, True)  # functional request, not resp


def test_can_filters_do_not_set_eff_flag_in_standard_mask():
    # Regression: putting the EFF flag (0x80000000) in an 11-bit filter mask
    # makes the kernel match nothing (verified on hardware). Standard-frame
    # masks must stay within 0x1FFFFFFF.
    cid, mask = single_id_filter(0x7ED, False)
    assert cid == 0x7ED and not (mask & 0x80000000)
    cid, mask = diag_response_filter(False)
    assert cid == 0x7E8 and not (mask & 0x80000000)
    # Extended filters DO carry the EFF flag in id and mask.
    cid, mask = single_id_filter(0x18DAF110, True)
    assert (cid & 0x80000000) and (mask & 0x80000000)


def test_addressing_profiles_selection():
    assert [p.name for p in _addressing_profiles(EdgeConfig(diag_addressing="11bit"))] == ["11bit"]
    assert [p.name for p in _addressing_profiles(EdgeConfig(diag_addressing="29bit"))] == ["29bit"]
    assert [p.name for p in _addressing_profiles(EdgeConfig(diag_addressing="both"))] == ["11bit", "29bit"]


# --------------------------------------------------------------------------- #
# A fake multi-responder bus
# --------------------------------------------------------------------------- #
class FakeMultiBus:
    """Minimal transport with ``request_all`` for testing the multi-ECU path.

    * An 11-bit engine ECU (resp 0x7E8) supports OBD PIDs 0x0C/0x0D and DID F190.
    * A 29-bit gateway ECU (0x18DAF110) answers DID F190/F187.
    Plain broadcast frames (0x003) are emitted by ``recv_frames`` and must never
    be mistaken for diagnostic responses.
    """

    VIN = "W1V4471234567890X"

    def request_all(self, tx_id, payload, timeout=1.0, expect_ids=None):
        payload = bytes(payload)
        out = []
        if payload[:2] == bytes([0x01, 0x00]):  # OBD supported PIDs 01-20
            if tx_id == OBD_FUNCTIONAL_TX:
                # bit for 0x0C and 0x0D set (bits 12 and 13 from base 0x00)
                mask = (1 << (32 - 0x0C)) | (1 << (32 - 0x0D))
                out.append((OBD_RESP_BASE, bytes([0x41, 0x00]) + mask.to_bytes(4, "big")))
        elif payload[0] == 0x01 and payload[1] in (0x0C, 0x0D):
            pid = payload[1]
            data = b"\x1a\xf8" if pid == 0x0C else b"\x40"
            out.append((OBD_RESP_BASE, bytes([0x41, pid]) + data))
        elif payload[0] == 0x22:  # UDS RDBI (real ECUs answer physically too)
            did = (payload[1] << 8) | payload[2]
            if tx_id in (OBD_FUNCTIONAL_TX, 0x7E0) and did == 0xF190:
                out.append((OBD_RESP_BASE, bytes([0x62, 0xF1, 0x90]) + self.VIN.encode()))
            if (tx_id in (OBD_FUNCTIONAL_TX_29, phys_req_29(0x10))
                    and did in (0xF190, 0xF187)):
                out.append((phys_resp_29(0x10),
                            bytes([0x62, payload[1], payload[2]]) + b"GW-42"))
        # filter like the real transport would
        if expect_ids is not None:
            out = [(rid, p) for rid, p in out if rid in expect_ids]
        return out

    def request(self, tx_id, rx_id, payload, timeout=1.0):
        for rid, pdu in self.request_all(tx_id, payload, timeout):
            if rid == rx_id:
                return pdu
        return None

    def recv_frames(self, timeout):
        yield Frame(arb_id=0x003, data=b"\x00\x61\x02\x03\x04\x05\x06\x07",
                    channel="can0", direction="rx")


def test_multi_responder_discovery_11_and_29_bit(discovery_schema):
    result = discover(FakeMultiBus(), mode="fast", config=EdgeConfig(diag_addressing="both"))
    jsonschema.validate(result, discovery_schema)

    # OBD: the 11-bit ECU's PIDs were found and sampled.
    assert set(result["obd"]["supported_pids"]) >= {"0x0C", "0x0D"}
    sampled = {s["pid"]: s for s in result["obd"]["samples"]}
    assert sampled["0x0D"]["value"] == 64.0  # 0x40 km/h
    assert any(r["addressing"] == "11bit" for r in result["obd"]["responders"])

    # UDS: both an 11-bit and a 29-bit ECU responded; VIN decoded as ASCII.
    addrs = {e["addressing"] for e in result["uds"]["ecus"]}
    assert addrs == {"11bit", "29bit"}
    vin_ecu = next(e for e in result["uds"]["ecus"] if "0xF190" in e["dids"]
                   and e["addressing"] == "11bit")
    assert vin_ecu["values"]["0xF190"] == FakeMultiBus.VIN


def test_census_ignores_direction_and_previews_ascii():
    census = plain_can_census(FakeMultiBus(), seconds=0.05)
    ids = {e["arb_id"]: e for e in census["arb_ids"]}
    assert "0x003" in ids
    assert ids["0x003"]["dlc"] == 8
    assert "sample_ascii" in ids["0x003"]


def test_decode_dtc():
    from canrosetta_edge.uds import decode_dtc
    assert decode_dtc(0x012345) == "P0123"
    assert decode_dtc(0xC12300).startswith("U")  # top 2 bits = 11 -> U
    assert decode_dtc(0x812300).startswith("B")  # 10 -> B


def test_active_session_is_guarded():
    import pytest
    from canrosetta_edge.active import (
        ActiveSession, assert_active_allowed, nrc_name, probe_extended_session,
    )
    with pytest.raises(PermissionError):
        assert_active_allowed(False)
    with pytest.raises(PermissionError):
        ActiveSession(FakeMultiBus(), 0x7E0, 0x7E8, allow=False)
    with pytest.raises(PermissionError):
        probe_extended_session(FakeMultiBus(), [(0x7E0, 0x7E8)], allow=False)
    assert "mfr-specific" in nrc_name(0xF1)
    assert assert_active_allowed(True) is None  # allowed: no raise


def test_format_report_smoke():
    result = discover(FakeMultiBus(), mode="fast", config=EdgeConfig(diag_addressing="both"))
    result["bus"] = {"interface": "can0", "bitrate": 500000,
                     "bitrate_source": "existing", "frames_sampled": 10, "unique_ids": 3}
    report = format_report(result)
    assert "CAN-Rosetta recon report" in report
    assert "can0 @ 500000" in report
    assert "OBD-II" in report and "UDS 0x22" in report and "Plain CAN" in report
