"""Read the dashboard video as timestamped frames.

Uses the ``phone/video_index.jsonl`` sidecar (written by the companion app) for
per-frame UTC timestamps — container timestamps are unreliable across decoders.
OpenCV is imported lazily; this module is only needed when actually running
perception over a real video, never for the label→reference wiring.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import numpy as np


def _load_index(index_path: Path) -> list[dict]:
    rows = []
    with index_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def frames(video_path: str | Path, index_path: str | Path,
           *, stride: int = 1) -> Iterator[tuple[float, np.ndarray]]:
    """Yield ``(t_utc, frame_bgr)`` pairs, taking every ``stride``-th frame.

    ``stride`` lets perception run at (say) 10 Hz over a 30 Hz video without
    decoding every frame's worth of work downstream.
    """
    try:
        import cv2  # type: ignore
    except ImportError as exc:  # noqa: TRY003
        raise ImportError(
            "reading video needs OpenCV: pip install opencv-python-headless"
        ) from exc

    index = _load_index(Path(index_path))
    cap = cv2.VideoCapture(str(video_path))
    try:
        i = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if i % stride == 0 and i < len(index):
                yield float(index[i]["t_utc"]), frame
            i += 1
    finally:
        cap.release()


def photos(index_path: str | Path, root: str | Path) -> Iterator[tuple[float, np.ndarray]]:
    """Yield ``(t_utc, image)`` from periodic high-resolution stills.

    The companion app captures full-resolution photos alongside the video (much
    sharper for OCR of small digits) and lists them in ``phone/photos_index.jsonl``
    as ``{t_utc, path}``. Images are decoded lazily via OpenCV.
    """
    try:
        import cv2  # type: ignore
    except ImportError as exc:  # noqa: TRY003
        raise ImportError(
            "reading photos needs OpenCV: pip install opencv-python-headless"
        ) from exc

    root = Path(root)
    for row in _load_index(Path(index_path)):
        img = cv2.imread(str(root / row["path"]))
        if img is not None:
            yield float(row["t_utc"]), img
