"""Pure frame operations: size cap, canvas fitting, placeholder and indicator banner.

Frames are BGR ``uint8`` arrays shaped ``(height, width, 3)``. These functions run on every
outgoing frame of the 33 ms presenting loop, so they avoid copying whole frames where possible.
"""

from __future__ import annotations

from functools import lru_cache
from typing import cast

import cv2
import numpy as np
from numpy.typing import NDArray

Frame = NDArray[np.uint8]

BACKGROUND: tuple[int, int, int] = (32, 32, 32)
"""Neutral colour (BGR) around recordings that do not fill the canvas."""

PLACEHOLDER_GREY: tuple[int, int, int] = (46, 46, 46)
"""Dark grey (BGR) of the "Camera paused" frame."""

_MAX_LONG_SIDE = 1920
_MAX_SHORT_SIDE = 1080
_INDICATOR_TEXT = "RECORDED VIDEO"
_FONT = cv2.FONT_HERSHEY_DUPLEX
_SYMBOL_COLOUR = (190, 190, 190)


def _even(value: float) -> int:
    """Round to the nearest integer, then down to an even number of at least 2."""
    return max(2, round(value) & ~1)


def cap_size(width: int, height: int) -> tuple[int, int]:
    """Return the stored size for a recording of ``width`` x ``height``.

    The longer side is capped at 1920 and the shorter at 1080 (so portrait keeps its
    orientation), aspect is kept, nothing is scaled up, and both sides come out even because
    video codecs need that.
    """
    scale = min(1.0, _MAX_LONG_SIDE / max(width, height), _MAX_SHORT_SIDE / min(width, height))
    if scale >= 1.0:
        return _even(width), _even(height)
    return _even(width * scale), _even(height * scale)


def fit_on_canvas(frame: Frame, canvas_w: int, canvas_h: int) -> Frame:
    """Place ``frame`` on a ``canvas_w`` x ``canvas_h`` canvas.

    A larger frame is scaled down to fit keeping aspect; a smaller one keeps its own size so
    it is never enlarged. It is centred on ``BACKGROUND``. A frame already at canvas size is
    returned as is, without a copy.
    """
    h, w = frame.shape[:2]
    if (w, h) == (canvas_w, canvas_h):
        return frame
    scale = min(1.0, canvas_w / w, canvas_h / h)
    if scale < 1.0:
        # INTER_AREA gives the cleanest result when shrinking; min() guards rounding overshoot.
        new_w = min(canvas_w, max(1, round(w * scale)))
        new_h = min(canvas_h, max(1, round(h * scale)))
        frame = cast(Frame, cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA))
        w, h = new_w, new_h
        if (w, h) == (canvas_w, canvas_h):
            return frame
    canvas = np.empty((canvas_h, canvas_w, 3), dtype=np.uint8)
    canvas[:] = BACKGROUND
    x = (canvas_w - w) // 2
    y = (canvas_h - h) // 2
    canvas[y : y + h, x : x + w] = frame
    return canvas


def _draw_camera_off(img: Frame, cx: int, cy: int, unit: int) -> None:
    """Draw a camera outline with a diagonal slash, centred on ``(cx, cy)``, in place.

    ``unit`` scales the symbol so it looks the same at every resolution.
    """
    thick = max(2, unit // 8)
    body_w, body_h = unit * 3, unit * 2
    x0, y0 = cx - body_w // 2 - unit // 3, cy - body_h // 2
    x1, y1 = x0 + body_w, y0 + body_h
    cv2.rectangle(img, (x0, y0), (x1, y1), _SYMBOL_COLOUR, thick, cv2.LINE_AA)
    lens = np.array(
        [
            [x1 + unit // 3, cy - unit // 3],
            [x1 + unit, cy - unit * 2 // 3],
            [x1 + unit, cy + unit * 2 // 3],
            [x1 + unit // 3, cy + unit // 3],
        ],
        dtype=np.int32,
    )
    cv2.polylines(img, [lens], True, _SYMBOL_COLOUR, thick, cv2.LINE_AA)
    # The slash overshoots the body so it reads as "off" at a glance. It is drawn in the
    # background grey first so it cuts a gap in the outline instead of merging into it.
    start = (x0 - unit // 2, y0 - unit // 2)
    end = (x1 + unit * 5 // 4, y1 + unit // 2)
    cv2.line(img, start, end, PLACEHOLDER_GREY, thick * 3, cv2.LINE_AA)
    cv2.line(img, start, end, _SYMBOL_COLOUR, thick, cv2.LINE_AA)


@lru_cache(maxsize=4)
def _placeholder(width: int, height: int) -> Frame:
    """Build and cache the placeholder for one size; the cached array is read-only."""
    img = np.empty((height, width, 3), dtype=np.uint8)
    img[:] = PLACEHOLDER_GREY
    unit = max(4, height // 24)
    text = "Camera paused"
    font_scale = height / 600
    thickness = max(1, round(height / 400))
    (text_w, text_h), baseline = cv2.getTextSize(text, _FONT, font_scale, thickness)
    gap = unit * 2
    # Symbol plus caption are centred as one block, not each on its own midpoint.
    block_h = unit * 4 + gap + text_h + baseline
    top = (height - block_h) // 2
    _draw_camera_off(img, width // 2, top + unit * 2, unit)
    text_y = top + unit * 4 + gap + text_h
    cv2.putText(
        img,
        text,
        ((width - text_w) // 2, text_y),
        _FONT,
        font_scale,
        _SYMBOL_COLOUR,
        thickness,
        cv2.LINE_AA,
    )
    img.flags.writeable = False
    return img


def placeholder(width: int, height: int) -> Frame:
    """Return the "Camera paused" frame for a canvas size.

    It is plain dark grey with a centred camera-off symbol and caption, and never contains
    anything derived from an input picture. The result is cached per size and read-only, so
    callers cannot corrupt it; copy it before drawing on it.
    """
    return _placeholder(width, height)


def _indicator_metrics(width: int, height: int) -> tuple[tuple[int, int, int, int], float, int]:
    """Return the banner region, text font scale and text thickness for a frame size."""
    banner_h = max(16, round(height * 0.06))
    margin = round(height * 0.03)
    font_scale = banner_h * 0.5 / 21.0  # DUPLEX cap height is about 21 px at scale 1.0
    thickness = max(1, round(banner_h / 18))
    (text_w, _), _ = cv2.getTextSize(_INDICATOR_TEXT, _FONT, font_scale, thickness)
    pad = banner_h // 2
    x0 = margin
    y1 = height - margin
    x1 = min(width, x0 + text_w + 2 * pad)
    return (x0, y1 - banner_h, x1, y1), font_scale, thickness


def indicator_region(width: int, height: int) -> tuple[int, int, int, int]:
    """Return ``(x0, y0, x1, y1)`` of the "RECORDED VIDEO" banner for a frame size."""
    return _indicator_metrics(width, height)[0]


def draw_indicator(frame: Frame) -> Frame:
    """Return a copy of ``frame`` with a semi-transparent "RECORDED VIDEO" banner lower-left.

    Only pixels inside ``indicator_region`` differ from the input, which is left untouched.
    """
    height, width = frame.shape[:2]
    (x0, y0, x1, y1), font_scale, thickness = _indicator_metrics(width, height)
    out = frame.copy()
    # Blend just the banner area (about 1% of the frame) rather than the whole picture.
    banner = (out[y0:y1, x0:x1] * 0.4).astype(np.uint8)
    banner += np.uint8(round(0.6 * 24))
    (_, text_h), _ = cv2.getTextSize(_INDICATOR_TEXT, _FONT, font_scale, thickness)
    pad = (y1 - y0) // 2
    cv2.putText(
        banner,
        _INDICATOR_TEXT,
        (pad, (y1 - y0 + text_h) // 2),
        _FONT,
        font_scale,
        (255, 255, 255),
        thickness,
        cv2.LINE_AA,
    )
    out[y0:y1, x0:x1] = banner
    return out
