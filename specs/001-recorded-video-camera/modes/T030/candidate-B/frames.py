"""Pure frame geometry and drawing: size cap, canvas fitting, placeholder and indicator.

Frames are BGR ``uint8`` arrays of shape ``(height, width, 3)``. Nothing here touches a device,
and nothing in the placeholder is derived from an input picture, so it can never show the user.
"""

from __future__ import annotations

from functools import lru_cache

import cv2
import numpy as np
from numpy.typing import NDArray

Frame = NDArray[np.uint8]

MAX_LONG_SIDE = 1920
MAX_SHORT_SIDE = 1080

# Neutral bars around a recording that does not fill the canvas (R6).
BACKGROUND = (24, 24, 24)
# Calm dark grey shown to meeting participants whenever nothing is presented (R7).
PLACEHOLDER_GREY = (48, 48, 48)

_SYMBOL_COLOUR = (190, 190, 190)
_TEXT_COLOUR = (210, 210, 210)
_FONT = cv2.FONT_HERSHEY_SIMPLEX
_BANNER_TEXT = "RECORDED VIDEO"
_BANNER_DIM = 0.35  # brightness kept under the banner; the rest is a dark overlay


def _even(value: float) -> int:
    """Round down to an even integer of at least 2 (video codecs need even sizes)."""
    return max(2, int(value) // 2 * 2)


def cap_size(width: int, height: int) -> tuple[int, int]:
    """Return the stored size: longer side <= 1920, shorter <= 1080, even, never enlarged.

    The two limits apply to the longer and shorter side separately so a portrait recording keeps
    its orientation instead of being squeezed into a landscape box.
    """
    long_side, short_side = max(width, height), min(width, height)
    scale = min(1.0, MAX_LONG_SIDE / long_side, MAX_SHORT_SIDE / short_side)
    # The epsilon stops float error from turning an exact 1920 into 1918.
    return _even(width * scale + 1e-6), _even(height * scale + 1e-6)


def fit_on_canvas(frame: Frame, canvas_w: int, canvas_h: int) -> Frame:
    """Place ``frame`` on a ``canvas_w`` x ``canvas_h`` canvas, keeping its aspect ratio.

    A frame that is larger in either direction is scaled down to fit; a smaller one keeps its own
    size and is centred on ``BACKGROUND``. A frame already canvas-sized is returned unchanged, so
    callers must not modify the result in place.
    """
    height, width = frame.shape[:2]
    if (width, height) == (canvas_w, canvas_h):
        return frame
    if width > canvas_w or height > canvas_h:
        scale = min(canvas_w / width, canvas_h / height)
        new_w = min(canvas_w, max(1, round(width * scale)))
        new_h = min(canvas_h, max(1, round(height * scale)))
        # INTER_AREA is the right filter for shrinking: it averages instead of dropping pixels.
        frame = np.asarray(
            cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA), dtype=np.uint8
        )
        width, height = new_w, new_h
    canvas: Frame = np.empty((canvas_h, canvas_w, 3), dtype=np.uint8)
    canvas[:] = BACKGROUND
    x0, y0 = (canvas_w - width) // 2, (canvas_h - height) // 2
    canvas[y0 : y0 + height, x0 : x0 + width] = frame
    return canvas


@lru_cache(maxsize=8)
def _build_placeholder(width: int, height: int) -> Frame:
    """Draw the "camera off" picture once per size."""
    img: Frame = np.empty((height, width, 3), dtype=np.uint8)
    img[:] = PLACEHOLDER_GREY
    unit = height / 720  # all measurements scale from the 720p design
    cx = width // 2
    thick = max(2, round(6 * unit))
    body_w, body_h = round(150 * unit), round(100 * unit)
    text_scale = 1.2 * unit
    text_thick = max(1, round(2 * unit))
    (text_w, text_h), baseline = cv2.getTextSize("Camera paused", _FONT, text_scale, text_thick)
    gap = round(44 * unit)
    group_h = body_h + gap + text_h + baseline
    top = (height - group_h) // 2

    # Camera body with a lens, then a diagonal slash to mean "off".
    bx0, by0 = cx - body_w // 2, top
    bx1, by1 = bx0 + body_w, top + body_h
    cv2.rectangle(img, (bx0, by0), (bx1, by1), _SYMBOL_COLOUR, thick, cv2.LINE_AA)
    cv2.circle(img, (cx, top + body_h // 2), round(24 * unit), _SYMBOL_COLOUR, thick, cv2.LINE_AA)
    pad = round(22 * unit)
    cv2.line(
        img,
        (bx0 - pad, by1 + pad),
        (bx1 + pad, by0 - pad),
        PLACEHOLDER_GREY,
        thick * 3,
        cv2.LINE_AA,
    )  # grey halo so the slash reads as cutting through the body
    cv2.line(
        img,
        (bx0 - pad, by1 + pad),
        (bx1 + pad, by0 - pad),
        _SYMBOL_COLOUR,
        thick,
        cv2.LINE_AA,
    )
    cv2.putText(
        img,
        "Camera paused",
        (cx - text_w // 2, top + body_h + gap + text_h),
        _FONT,
        text_scale,
        _TEXT_COLOUR,
        text_thick,
        cv2.LINE_AA,
    )
    img.flags.writeable = False
    return img


def placeholder(width: int, height: int) -> Frame:
    """Return the dark-grey "Camera paused" frame for this size.

    The array is cached and read-only so repeated calls are free and callers cannot corrupt it;
    copy it before drawing on it.
    """
    return _build_placeholder(width, height)


@lru_cache(maxsize=8)
def _indicator_layout(width: int, height: int) -> tuple[tuple[int, int, int, int], float, int]:
    """Return the banner region, font scale and text thickness for a frame size."""
    banner_h = round(height * 0.06)
    margin = round(height * 0.03)
    text_scale = banner_h * 0.5 / 21  # 21 px is the cap height of scale 1.0 in this font
    thick = max(1, round(banner_h / 22))
    (text_w, _), _ = cv2.getTextSize(_BANNER_TEXT, _FONT, text_scale, thick)
    dot_space = round(banner_h * 0.9)  # room for the red dot left of the text
    banner_w = min(width - margin, text_w + dot_space + round(banner_h * 0.5))
    x0, y1 = margin, height - margin
    return (x0, y1 - banner_h, x0 + banner_w, y1), text_scale, thick


def indicator_region(width: int, height: int) -> tuple[int, int, int, int]:
    """Return ``(x0, y0, x1, y1)`` of the lower-left banner; sized from the frame height."""
    return _indicator_layout(width, height)[0]


def draw_indicator(frame: Frame) -> Frame:
    """Return a copy of ``frame`` with a semi-transparent "RECORDED VIDEO" banner.

    Only pixels inside ``indicator_region`` differ from the input, which is left untouched.
    """
    height, width = frame.shape[:2]
    (x0, y0, x1, y1), text_scale, thick = _indicator_layout(width, height)
    out = frame.copy()
    # Darkening only the small banner keeps this cheap enough for every frame at 30 fps.
    banner = cv2.convertScaleAbs(frame[y0:y1, x0:x1], alpha=_BANNER_DIM)
    banner_h = y1 - y0
    radius = max(2, round(banner_h * 0.14))
    cv2.circle(
        banner, (round(banner_h * 0.5), banner_h // 2), radius, (40, 40, 230), -1, cv2.LINE_AA
    )
    (_, text_h), _ = cv2.getTextSize(_BANNER_TEXT, _FONT, text_scale, thick)
    cv2.putText(
        banner,
        _BANNER_TEXT,
        (round(banner_h * 0.9), (banner_h + text_h) // 2 - round(banner_h * 0.07)),
        _FONT,
        text_scale,
        (255, 255, 255),
        thick,
        cv2.LINE_AA,
    )
    out[y0:y1, x0:x1] = banner
    return out
