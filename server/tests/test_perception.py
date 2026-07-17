"""Unit tests for the perception extractors (numpy-only paths, no video/OCR)."""

import numpy as np

from canrosetta.perception import needle_angle_deg, telltale_on
from canrosetta.perception.extractors import (
    DigitExtractor,
    GearExtractor,
    NeedleExtractor,
    TelltaleExtractor,
    angle_to_value,
)
from canrosetta.perception.rois import ROI, ROISet


def test_telltale_on_off():
    lit = np.full((10, 10, 3), 240, dtype=np.uint8)
    dark = np.full((10, 10, 3), 10, dtype=np.uint8)
    assert telltale_on(lit) is True
    assert telltale_on(dark) is False
    assert TelltaleExtractor().extract(lit) == 1.0
    # red-channel selection for a coloured lamp
    red = np.zeros((10, 10, 3), dtype=np.uint8)
    red[..., 2] = 250
    assert telltale_on(red, channel=2) is True
    assert telltale_on(red, channel=1) is False


def test_needle_angle_recovered():
    img = np.full((41, 41), 255.0)  # light gauge face
    cx = cy = 20
    for t in np.linspace(-15, 15, 60):
        x = int(round(cx + t * np.cos(np.radians(30))))
        y = int(round(cy + t * np.sin(np.radians(30))))
        img[y - 1:y + 2, x - 1:x + 2] = 0.0  # dark needle
    ang = needle_angle_deg(img)
    assert ang is not None
    assert min(abs(ang - 30), abs(ang - 210)) < 8  # within a few degrees (mod 180)


def test_needle_calibration_and_extractor():
    params = {"angle_min": 0, "angle_max": 180, "value_min": 0, "value_max": 8000}
    assert abs(angle_to_value(90, params) - 4000) < 1e-6
    blank = np.full((20, 20), 255.0)
    assert NeedleExtractor().extract(blank) is None  # no needle -> None


def test_digit_and_gear_extractors_with_stub_ocr():
    speed = DigitExtractor(lambda _img: "60 km/h")
    assert speed.extract(np.zeros((8, 8))) == 60.0
    gear = GearExtractor(lambda _img: "D")
    assert gear.extract(np.zeros((8, 8))) == 2.0
    assert GearExtractor(lambda _img: "3").extract(np.zeros((8, 8))) == 3.0
    assert DigitExtractor(lambda _img: "").extract(np.zeros((8, 8))) is None


def test_perceive_with_no_rois_is_a_noop(tmp_path):
    # exercises the orchestrator skeleton without pulling in video/OCR backends
    from canrosetta.perception.run import perceive

    (tmp_path / "can").mkdir()
    counts = perceive(tmp_path, ROISet([]))
    assert counts == {}


def test_roi_set_roundtrip_and_crop():
    rs = ROISet.from_dict({"rois": [
        {"name": "dash_speed", "kind": "digits", "box": [10, 20, 30, 12], "params": {"scale": 1}},
        {"name": "turn_left", "kind": "telltale", "box": [0, 0, 5, 5]},
    ]})
    assert len(rs.rois) == 2
    assert ROISet.from_dict(rs.to_dict()).rois[0].name == "dash_speed"
    frame = np.arange(100 * 100).reshape(100, 100)
    roi = ROI("x", "telltale", (10, 20, 30, 12))
    assert roi.crop(frame).shape == (12, 30)
