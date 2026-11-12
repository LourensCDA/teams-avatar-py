"""Frame geometry and overlays: size cap, fixed canvas, paused placeholder, indicator banner.

Frames are BGR ``uint8`` arrays shaped ``(height, width, 3)``. These functions run on every
outgoing frame in a 33 ms loop, so they touch only the pixels they must.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Final, cast

import cv2
import numpy as np
import numpy.typing as npt

Frame = npt.NDArray[np.uint8]

BACKGROUND: Final[tuple[int, int, int]] = (32, 32, 32)
PLACEHOLDER_GREY: Final[tuple[int, int, int]] = (45, 45, 45)

_SYMBOL_COLOUR: Final[tuple[int, int, int]] = (170, 170, 170)
_FONT: Final[int] = cv2.FONT_HERSHEY_DUPLEX
_MAX_LONG: Final[int] = 1920
_MAX_SHORT: Final[int] = 1080
_BANNER_DIM: Final[float] = 0.4


def cap_size(width: int, height: int) -> tuple[int, int]:
    """Return the stored size: longer side <= 1920, shorter <= 1080, even, never enlarged.

    The two limits are applied to the long and short side (not to width and height) so a
    portrait recording keeps its orientation.
    """
    long_side, short_side = max(width, height), min(width, height)
    scale = min(1.0, _MAX_LONG / long_side, _MAX_SHORT / short_side)
    # Floor to even: 4:2:0 video encoders reject odd dimensions.
    return max(2, int(width * scale) // 2 * 2), max(2, int(height * scale) // 2 * 2)


def fit_on_canvas(frame: Frame, canvas_w: int, canvas_h: int) -> Frame:
    """Place ``frame`` on a ``canvas_w`` x ``canvas_h`` canvas, centred, keeping aspect.

    Larger frames are scaled down to fit; frames that already fit are shown at their own size
    on ``BACKGROUND`` and never enlarged. A frame of exactly canvas size is returned as is.
    """
    h, w = frame.shape[:2]
    if (w, h) == (canvas_w, canvas_h):
        return frame
    if w > canvas_w or h > canvas_h:
        scale = min(canvas_w / w, canvas_h / h)
        w = min(canvas_w, max(1, round(w * scale)))
        h = min(canvas_h, max(1, round(h * scale)))
        # INTER_AREA avoids the aliasing bilinear shows on large downscales.
        frame = cast("Frame", cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA))
        if (w, h) == (canvas_w, canvas_h):
            return frame
    canvas = np.empty((canvas_h, canvas_w, 3), dtype=np.uint8)
    canvas[:] = BACKGROUND
    x, y = (canvas_w - w) // 2, (canvas_h - h) // 2
    canvas[y : y + h, x : x + w] = frame
    return canvas


def _draw_text(img: Frame, text: str, origin: tuple[int, int], scale: float, thick: int) -> None:
    """Draw anti-aliased light text with its baseline-left corner at ``origin``."""
    cv2.putText(img, text, origin, _FONT, scale, (235, 235, 235), thick, cv2.LINE_AA)


@lru_cache(maxsize=4)
def _placeholder(width: int, height: int) -> Frame:
    """Render the paused placeholder once per size; the result is read-only."""
    img = np.empty((height, width, 3), dtype=np.uint8)
    img[:] = PLACEHOLDER_GREY
    cx = width // 2

    text = "Camera paused"
    scale = height / 720 * 0.9
    thick = max(1, round(height / 360))
    (tw, th), _ = cv2.getTextSize(text, _FONT, scale, thick)

    # Icon and caption form one group so the whole block is vertically centred.
    icon_h = round(height * 0.14)
    icon_w = round(icon_h * 1.5)
    gap = round(height * 0.05)
    top = (height - (icon_h + gap + th)) // 2
    line = max(2, round(height / 144))

    bx0, by0 = cx - icon_w // 2, top
    body = (bx0, by0, bx0 + round(icon_w * 0.72), by0 + icon_h)
    cv2.rectangle(img, body[:2], body[2:], _SYMBOL_COLOUR, line, cv2.LINE_AA)
    cv2.circle(
        img,
        ((body[0] + body[2]) // 2, (body[1] + body[3]) // 2),
        round(icon_h * 0.22),
        _SYMBOL_COLOUR,
        line,
        cv2.LINE_AA,
    )
    mid = (body[1] + body[3]) // 2
    tip = bx0 + icon_w
    lens = np.array([[body[2], mid], [tip, body[1] + icon_h // 6], [tip, body[3] - icon_h // 6]])
    cv2.polylines(img, [lens], True, _SYMBOL_COLOUR, line, cv2.LINE_AA)

    # Slash: a grey stroke under the light one cuts a clean gap in the outline.
    p1 = (bx0 - icon_h // 6, by0 - icon_h // 8)
    p2 = (tip + icon_h // 6, by0 + icon_h + icon_h // 8)
    cv2.line(img, p1, p2, PLACEHOLDER_GREY, line * 3, cv2.LINE_AA)
    cv2.line(img, p1, p2, _SYMBOL_COLOUR, line, cv2.LINE_AA)

    _draw_text(img, text, (cx - tw // 2, top + icon_h + gap + th), scale, thick)
    img.setflags(write=False)
    return img


def placeholder(width: int, height: int) -> Frame:
    """Return the "Camera paused" frame for this size.

    It is plain dark grey with a camera-off symbol and caption, drawn from nothing but
    constants, so it can never show the user. Cached per size; the array is read-only so a
    caller cannot corrupt the cache (use ``.copy()`` if you need to draw on it).
    """
    return _placeholder(width, height)


@lru_cache(maxsize=8)
def _banner_layout(height: int) -> tuple[int, int, int, float, int]:
    """Return (banner width, banner height, margin, font scale, thickness) for a frame height."""
    banner_h = round(height * 0.06)
    margin = round(height * 0.03)
    thick = max(1, round(height / 540))
    # Fit the caption to ~50% of the banner height, whatever the font's base size.
    (_, base_h), _ = cv2.getTextSize("RECORDED VIDEO", _FONT, 1.0, thick)
    scale = banner_h * 0.5 / base_h
    (tw, _), _ = cv2.getTextSize("RECORDED VIDEO", _FONT, scale, thick)
    pad = round(banner_h * 0.4)
    dot = round(banner_h * 0.3)
    return pad + dot + pad // 2 + tw + pad, banner_h, margin, scale, thick


def indicator_region(width: int, height: int) -> tuple[int, int, int, int]:
    """Return ``(x0, y0, x1, y1)`` of the lower-left "RECORDED VIDEO" banner."""
    banner_w, banner_h, margin, _, _ = _banner_layout(height)
    x0, y1 = margin, height - margin
    return x0, y1 - banner_h, min(width, x0 + banner_w), y1


def draw_indicator(frame: Frame) -> Frame:
    """Return a copy of ``frame`` with a semi-transparent "RECORDED VIDEO" banner.

    Only pixels inside ``indicator_region`` change, and only that region is blended.
    """
    h, w = frame.shape[:2]
    _, banner_h, _, scale, thick = _banner_layout(h)
    x0, y0, x1, y1 = indicator_region(w, h)
    out = frame.copy()
    roi = out[y0:y1, x0:x1]
    # Darkening (not a solid fill) keeps the banner see-through yet text stays readable.
    cv2.convertScaleAbs(roi, dst=roi, alpha=_BANNER_DIM)
    pad = round(banner_h * 0.4)
    dot_r = round(banner_h * 0.15)
    mid_y = banner_h // 2
    cv2.circle(roi, (pad + dot_r, mid_y), dot_r, (60, 60, 230), -1, cv2.LINE_AA)
    (_, th), _ = cv2.getTextSize("RECORDED VIDEO", _FONT, scale, thick)
    _draw_text(roi, "RECORDED VIDEO", (pad + 2 * dot_r + pad // 2, mid_y + th // 2), scale, thick)
    return out
