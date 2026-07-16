"""The read-only guards refuse anything but safe read services."""

import pytest

from canrosetta_edge.obd import SAFE_OBD_MODES, assert_read_only_mode
from canrosetta_edge.transport import SimulatedTransport
from canrosetta_edge.uds import UdsClient, assert_read_only_service


@pytest.mark.parametrize("sid", [
    0x2E,  # WriteDataByIdentifier
    0x31,  # RoutineControl
    0x2F,  # InputOutputControl
    0x10,  # DiagnosticSessionControl
    0x27,  # SecurityAccess
    0x11,  # ECUReset
])
def test_uds_guard_rejects_write_services(sid):
    with pytest.raises(ValueError):
        assert_read_only_service(sid)


def test_uds_client_send_request_rejects_non_read_service():
    with SimulatedTransport() as t:
        client = UdsClient(t)
        with pytest.raises(ValueError):
            client.send_request(0x2E, b"\xf1\x90\x01")  # write attempt


def test_uds_guard_allows_read_services():
    assert_read_only_service(0x22)  # ReadDataByIdentifier
    assert_read_only_service(0x19)  # ReadDTCInformation


def test_obd_guard_rejects_non_read_modes():
    for mode in (0x02, 0x03, 0x04, 0x08):
        if mode in SAFE_OBD_MODES:
            continue
        with pytest.raises(ValueError):
            assert_read_only_mode(mode)
    assert_read_only_mode(0x01)  # current data -- allowed
    assert_read_only_mode(0x09)  # vehicle info -- allowed
