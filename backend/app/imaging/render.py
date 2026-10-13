"""Slice -> PNG rendering and default windowing."""
from __future__ import annotations

import io

import numpy as np
from PIL import Image

from ..errors import AppError

ORANGE = (255, 128, 0)


def default_window(data: np.ndarray) -> dict:
    finite = data[np.isfinite(data)]
    nz = finite[finite != 0]
    src = nz if nz.size else finite
    lo, hi = (float(x) for x in np.percentile(src, [1, 99])) if src.size else (0.0, 0.0)
    if hi <= lo:
        lo, hi = (float(src.min()), float(src.max())) if src.size else (0.0, 0.0)
    if hi <= lo:
        return {"center": lo, "width": 1.0, "p1": lo, "p99": hi}
    return {"center": (lo + hi) / 2.0, "width": hi - lo, "p1": lo, "p99": hi}


def window_to_u8(raster: np.ndarray, center: float, width: float) -> np.ndarray:
    if not (np.isfinite(center) and np.isfinite(width)) or width <= 0:
        raise AppError(422, "INVALID_WINDOW", "Window width must be positive and values finite.")
    lo = center - width / 2.0
    scaled = np.clip((raster.astype(np.float64) - lo) / width, 0.0, 1.0)
    return np.rint(scaled * 255.0).astype(np.uint8)


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=1)
    return buf.getvalue()


def gray_png(u8: np.ndarray) -> bytes:
    return _png(Image.fromarray(u8, mode="L"))


def mask_png(raster: np.ndarray) -> bytes:
    h, w = raster.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    on = raster > 0
    rgba[on, 0], rgba[on, 1], rgba[on, 2], rgba[on, 3] = ORANGE[0], ORANGE[1], ORANGE[2], 255
    return _png(Image.fromarray(rgba, mode="RGBA"))
