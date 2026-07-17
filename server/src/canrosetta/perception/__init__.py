"""Dashboard-video perception → timestamped label streams.

The companion phone films the dashboard; this turns that video into *reference
signals* the identifier can correlate against CAN bits — the same role GPS/IMU/OBD
play, but for quantities no OBD PID exposes (fuel gauge, warning lamps, gear).

Design choice (see docs/methodology.md): we do **not** feed pixels into the CAN
model. We extract a structured, timestamped label stream per dashboard element
and treat those as references. The dashboard is heterogeneous, so there is one
extractor per element *kind*:

- digits (speed/RPM/odometer/temperature) → OCR → continuous reference
- telltales / indicators (turn signal, ABS, engine lamp) → on/off + blink →
  event reference (matches a bit flip)
- analog needles → needle-angle → continuous reference
- gear (PRND / number) → OCR/classifier → categorical reference

Heavy backends (OpenCV, an OCR engine) are imported lazily so the package — and
the label→reference wiring the server depends on — works without them. The
numpy-only extractor logic (telltale threshold, needle angle) is unit-tested.
"""

from .extractors import (
    DigitExtractor,
    Extractor,
    GearExtractor,
    NeedleExtractor,
    TelltaleExtractor,
    needle_angle_deg,
    telltale_on,
)
from .rois import ROI, ROISet

__all__ = [
    "ROI",
    "ROISet",
    "Extractor",
    "DigitExtractor",
    "TelltaleExtractor",
    "NeedleExtractor",
    "GearExtractor",
    "telltale_on",
    "needle_angle_deg",
]
