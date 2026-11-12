"""Synthetic test clips: frames carry their own index so tests can tell which frame they got."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import av
import numpy as np
import numpy.typing as npt

Frame = npt.NDArray[np.uint8]

COUNTER_BITS = 12


def counter_frame(index: int, width: int, height: int, distinct: bool = True) -> Frame:
    """Draw a BGR frame whose top band encodes ``index`` as black/white blocks.

    Blocks rather than digits are used so the index survives lossy H.264 encoding and can be
    read back with a simple threshold (see ``read_counter``).

    Args:
        index: Frame number to encode (0 when ``distinct`` is false).
        width: Frame width in pixels.
        height: Frame height in pixels.
        distinct: When false every frame is identical (a still clip).

    Returns:
        A ``(height, width, 3)`` uint8 array.
    """
    value = index if distinct else 0
    frame = np.empty((height, width, 3), dtype=np.uint8)
    # Background level changes with the index so first and last frames differ in a long clip.
    frame[:] = (40 + (value * 3) % 160, 90, 140)
    band = max(height // 8, 2)
    block = width // COUNTER_BITS
    for bit in range(COUNTER_BITS):
        on = (value >> bit) & 1
        frame[:band, bit * block : (bit + 1) * block] = 255 if on else 0
    return frame


def read_counter(frame: npt.NDArray[Any]) -> int:
    """Read back the index drawn by ``counter_frame`` (assumes the clip fills the frame)."""
    height, width = frame.shape[:2]
    band = max(height // 8, 2)
    block = width // COUNTER_BITS
    value = 0
    for bit in range(COUNTER_BITS):
        cell = frame[
            band // 4 : band - band // 4, bit * block + block // 4 : (bit + 1) * block - block // 4
        ]
        if float(cell.mean()) > 128:
            value |= 1 << bit
    return value


def write_clip(
    path: Path, frames: int, width: int, height: int, fps: int = 30, distinct: bool = True
) -> Path:
    """Write an H.264 MP4 of ``frames`` counter frames with PyAV.

    Args:
        path: Output file.
        frames: Number of frames.
        width: Frame width (even).
        height: Frame height (even).
        fps: Frame rate.
        distinct: When false every frame is identical.

    Returns:
        ``path``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with av.open(str(path), mode="w") as container:
        stream = container.add_stream("libx264", rate=fps)
        stream.width = width
        stream.height = height
        stream.pix_fmt = "yuv420p"
        stream.options = {
            "crf": "18",
            "preset": "veryfast",
            "g": "30",
            "keyint_min": "30",
            "sc_threshold": "0",
        }
        for i in range(frames):
            image = counter_frame(i, width, height, distinct)
            video_frame = av.VideoFrame.from_ndarray(image, format="bgr24")
            for packet in stream.encode(video_frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path
