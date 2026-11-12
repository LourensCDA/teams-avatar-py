"""Save-time checks that warn about recordings that will loop badly (FR-015, research R5)."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt

from teams_avatar.adapters.pyav_codec import PyAVReader
from teams_avatar.models import RecordingWarning

# Mean absolute difference (0-255) between first and last frames, at 160x90 greyscale, above
# which the 1-second crossfade is likely to show a double image. Tuned in T071 (research R5): real
# recordings scoring 7.3, 8.5 and 39.3 (sitting -> standing) all blended without a visible double
# image in Teams, so the warning starts just above the highest score seen to be fine.
GHOST_THRESHOLD = 40
TOO_SHORT_S = 5.0
_COMPARE_SIZE = (160, 90)

Frame = npt.NDArray[np.uint8]


def _small_grey(frame: Frame) -> npt.NDArray[np.float32]:
    """Downscale to 160x90 greyscale so noise and tiny details do not count as movement."""
    grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(grey, _COMPARE_SIZE, interpolation=cv2.INTER_AREA)
    return np.asarray(small, dtype=np.float32)


def ghost_score(first: Frame, last: Frame) -> float:
    """Return the mean absolute difference of two frames at 160x90 greyscale."""
    return float(np.mean(np.abs(_small_grey(first) - _small_grey(last))))


def warnings_for(duration_s: float, first: Frame, last: Frame) -> list[RecordingWarning]:
    """Return the warnings for a recording of ``duration_s`` with these first and last frames."""
    result: list[RecordingWarning] = []
    if duration_s < TOO_SHORT_S:
        result.append("too_short")
    if ghost_score(first, last) > GHOST_THRESHOLD:
        result.append("loop_ghosting")
    return result


def compute_warnings(path: Path) -> list[RecordingWarning]:
    """Read a stored video's first and last frames and return its warnings.

    Raises:
        UnreadableVideoError: If the file cannot be decoded.
    """
    with PyAVReader(path) as reader:
        first = reader.read()
        reader.seek(reader.frame_count - 1)
        last = reader.read()
        duration = reader.frame_count / reader.fps
    if first is None or last is None:
        return ["too_short"] if duration < TOO_SHORT_S else []
    return warnings_for(duration, first, last)
