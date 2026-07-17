"""Run perception over a session's dashboard video → ``labels/*.jsonl``.

Ties the pieces together: for each ROI, sample its extractor at every (strided)
video frame and append a timestamped record to the right label stream. The
label streams are on the companion clock (the phone filmed them) and are picked
up by the identifier exactly like any other reference.
"""

from __future__ import annotations

import json
from pathlib import Path

from .extractors import (
    DigitExtractor,
    GearExtractor,
    NeedleExtractor,
    TelltaleExtractor,
    default_ocr,
)
from .rois import DIGITS, GEAR, NEEDLE, TELLTALE, ROISet

# which label file each ROI kind writes to
_STREAM = {DIGITS: "dashboard_ocr.jsonl", TELLTALE: "telltales.jsonl",
           NEEDLE: "dashboard_ocr.jsonl", GEAR: "gear.jsonl"}


def _build_extractor(roi, ocr):
    if roi.kind == TELLTALE:
        return TelltaleExtractor(threshold=roi.params.get("threshold", 0.5),
                                 channel=roi.params.get("channel"))
    if roi.kind == NEEDLE:
        return NeedleExtractor(params=roi.params)
    if roi.kind == DIGITS:
        return DigitExtractor(ocr(), scale=roi.params.get("scale", 1.0))
    if roi.kind == GEAR:
        return GearExtractor(ocr())
    raise ValueError(f"unknown ROI kind {roi.kind!r}")


def perceive(session_dir: str | Path, roi_set: ROISet | None = None,
             *, stride: int = 3, ocr=default_ocr) -> dict[str, int]:
    """Run all ROI extractors over the session video, writing label streams.

    Returns a per-stream record count. Requires the video backends; this is the
    real-video entry point (the extractor logic itself is tested without video).
    """
    from . import video

    root = Path(session_dir)
    roi_set = roi_set or ROISet.from_json(root / "perception.json")
    labels_dir = root / "labels"
    labels_dir.mkdir(parents=True, exist_ok=True)
    ocr_factory = ocr

    photos_index = root / "phone" / "photos_index.jsonl"
    have_photos = photos_index.exists()

    def prefers_photos(roi) -> bool:
        # numeric/gear OCR benefits from high-res stills; motion/state from video.
        pref = roi.params.get("source", "digits_on_photos")
        if pref == "photos":
            return True
        if pref == "video":
            return False
        return roi.kind in (DIGITS, GEAR)

    photo_rois = [r for r in roi_set.rois if have_photos and prefers_photos(r)]
    video_rois = [r for r in roi_set.rois if r not in photo_rois]

    # truncate the label streams we're about to (re)write so re-runs don't append
    for stream in {_STREAM[r.kind] for r in roi_set.rois}:
        (labels_dir / stream).unlink(missing_ok=True)

    handles: dict[str, object] = {}
    counts: dict[str, int] = {}

    def run_over(rois, frame_iter):
        exts = [(r, _build_extractor(r, ocr_factory)) for r in rois]
        for t_utc, frame in frame_iter:
            for roi, ex in exts:
                val = ex.extract(roi.crop(frame))
                if val is None:
                    continue
                stream = _STREAM[roi.kind]
                fh = handles.get(stream)
                if fh is None:
                    fh = (labels_dir / stream).open("a", encoding="utf-8")
                    handles[stream] = fh
                fh.write(json.dumps(_record(roi, t_utc, val)) + "\n")  # type: ignore[attr-defined]
                counts[roi.name] = counts.get(roi.name, 0) + 1

    try:
        if video_rois:
            run_over(video_rois, video.frames(root / "phone" / "video.mp4",
                                              root / "phone" / "video_index.jsonl", stride=stride))
        if photo_rois:
            run_over(photo_rois, video.photos(photos_index, root))
    finally:
        for fh in handles.values():
            fh.close()  # type: ignore[attr-defined]
    return counts


def _record(roi, t_utc: float, val: float) -> dict:
    if roi.kind == TELLTALE:
        return {"t_utc": t_utc, "name": roi.name, "state": int(val)}
    if roi.kind == GEAR:
        return {"t_utc": t_utc, "gear": val}
    return {"t_utc": t_utc, "field": roi.name, "value": val}
