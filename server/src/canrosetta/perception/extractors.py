"""Per-element extractors: a cropped ROI image → a value/state.

The two extractors that don't need a heavy backend (telltale on/off, needle
angle) are pure numpy and unit-tested. Digits and gear need an OCR engine, which
is injected as a callable so the package never hard-depends on one and tests can
pass a stub.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

import numpy as np


def _gray(roi: np.ndarray) -> np.ndarray:
    """Return a float grayscale view of an ROI (accepts H×W or H×W×3)."""
    a = np.asarray(roi, dtype=np.float64)
    if a.ndim == 3:
        # Rec. 601 luma; also works for BGR/RGB since weights are symmetric enough
        a = a[..., :3].mean(axis=2)
    return a


def telltale_on(roi: np.ndarray, *, threshold: float = 0.5,
                channel: int | None = None) -> bool:
    """Is an indicator lamp lit? Mean ROI intensity (0..1) over ``threshold``.

    ``channel`` selects a colour plane for coloured lamps (e.g. 2 for the red
    channel of a warning light); default uses luma. Robust to absolute
    brightness by normalising to [0,1].
    """
    a = np.asarray(roi, dtype=np.float64)
    if channel is not None and a.ndim == 3:
        a = a[..., channel]
    else:
        a = _gray(a)
    if a.size == 0:
        return False
    norm = a / 255.0 if a.max() > 1.0 else a
    return bool(norm.mean() > threshold)


def needle_angle_deg(roi: np.ndarray, *, dark_needle: bool = True) -> float | None:
    """Estimate an analog needle's angle (degrees) via PCA of needle pixels.

    Selects the needle pixels (dark on a light gauge, or bright if
    ``dark_needle=False``) by Otsu-ish median thresholding, then takes the major
    axis of their coordinate covariance. Angle is measured CCW from the +x axis
    in image coordinates, in [0, 180). Returns None if no clear needle.
    """
    g = _gray(roi)
    if g.size < 9:
        return None
    thr = (g.max() + g.min()) / 2.0
    mask = g < thr if dark_needle else g > thr
    ys, xs = np.nonzero(mask)
    if len(xs) < 5:
        return None
    pts = np.vstack([xs - xs.mean(), ys - ys.mean()]).astype(np.float64)
    cov = pts @ pts.T
    evals, evecs = np.linalg.eigh(cov)
    vx, vy = evecs[:, int(np.argmax(evals))]
    angle = np.degrees(np.arctan2(vy, vx)) % 180.0
    return float(angle)


def angle_to_value(angle_deg: float, params: dict) -> float:
    """Map a needle angle to a physical value via a linear calibration.

    ``params`` gives ``angle_min``/``angle_max`` and ``value_min``/``value_max``.
    """
    a0 = params.get("angle_min", 0.0)
    a1 = params.get("angle_max", 180.0)
    v0 = params.get("value_min", 0.0)
    v1 = params.get("value_max", 1.0)
    if a1 == a0:
        return v0
    frac = (angle_deg - a0) / (a1 - a0)
    return v0 + frac * (v1 - v0)


# OCR is injected: a callable (roi_image) -> recognized string.
OcrBackend = Callable[[np.ndarray], str]


class Extractor(Protocol):
    kind: str

    def extract(self, roi: np.ndarray) -> float | None:
        ...


class TelltaleExtractor:
    kind = "telltale"

    def __init__(self, threshold: float = 0.5, channel: int | None = None):
        self.threshold = threshold
        self.channel = channel

    def extract(self, roi: np.ndarray) -> float:
        return 1.0 if telltale_on(roi, threshold=self.threshold, channel=self.channel) else 0.0


class NeedleExtractor:
    kind = "needle"

    def __init__(self, params: dict | None = None, dark_needle: bool = True):
        self.params = params or {}
        self.dark_needle = dark_needle

    def extract(self, roi: np.ndarray) -> float | None:
        ang = needle_angle_deg(roi, dark_needle=self.dark_needle)
        return None if ang is None else angle_to_value(ang, self.params)


class DigitExtractor:
    kind = "digits"

    def __init__(self, ocr: OcrBackend, scale: float = 1.0):
        self.ocr = ocr
        self.scale = scale

    def extract(self, roi: np.ndarray) -> float | None:
        text = self.ocr(roi)
        digits = "".join(c for c in text if c.isdigit() or c in ".-")
        try:
            return float(digits) * self.scale if digits else None
        except ValueError:
            return None


class GearExtractor:
    kind = "gear"
    # P/R/N/D map to ordered codes; a numeric gear ("3") stays numeric.
    _MAP = {"P": 0.0, "R": -1.0, "N": 1.0, "D": 2.0}

    def __init__(self, ocr: OcrBackend):
        self.ocr = ocr

    def extract(self, roi: np.ndarray) -> float | None:
        text = self.ocr(roi).strip().upper()
        for ch in text:
            if ch in self._MAP:
                return self._MAP[ch]
            if ch.isdigit():
                return float(ch)
        return None


def default_ocr() -> OcrBackend:
    """Lazily build an OCR backend from an installed engine (pytesseract).

    Kept out of import time so the package works without any OCR engine.
    """
    try:
        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore
    except ImportError as exc:  # noqa: TRY003
        raise ImportError(
            "digit/gear OCR needs an engine: pip install pytesseract pillow "
            "(and the tesseract binary). Perception can still run telltale/needle "
            "extractors without it."
        ) from exc

    def ocr(roi: np.ndarray) -> str:
        arr = np.asarray(roi, dtype=np.uint8)
        return pytesseract.image_to_string(
            Image.fromarray(arr), config="--psm 7 -c tessedit_char_whitelist=0123456789.PRND"
        )

    return ocr
