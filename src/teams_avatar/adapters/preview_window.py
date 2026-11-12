"""OpenCV windows for the live recording preview (FR-001).

Only ``record`` uses this module, never the camera service. The window shows the camera with a
red "REC" mark and the elapsed time; Space or Enter saves, Esc or closing the window cancels.
"""

from __future__ import annotations

import cv2
import numpy as np
import numpy.typing as npt

Frame = npt.NDArray[np.uint8]

RECORD_WINDOW = "Teams Avatar - recording (Space/Enter: save, Esc: cancel)"
_SAVE_KEYS = {32, 13}
_CANCEL_KEYS = {27}
_RED = (40, 40, 230)
_WHITE = (255, 255, 255)


def key_action(code: int) -> str | None:
    """Map an OpenCV key code to ``"save"``, ``"cancel"`` or None."""
    key = code & 0xFF if code >= 0 else -1
    if key in _SAVE_KEYS:
        return "save"
    if key in _CANCEL_KEYS:
        return "cancel"
    return None


def draw_rec_overlay(frame: Frame, elapsed_s: float) -> Frame:
    """Return a copy of ``frame`` with a red dot, "REC" and ``m:ss`` in the top-left corner."""
    out = frame.copy()
    height = out.shape[0]
    unit = height / 720
    radius = max(4, round(12 * unit))
    margin = round(24 * unit)
    centre = (margin + radius, margin + radius)
    cv2.circle(out, centre, radius, _RED, -1, cv2.LINE_AA)
    total = int(elapsed_s)
    text = f"REC  {total // 60}:{total % 60:02d}"
    scale = 0.9 * unit
    thick = max(1, round(2 * unit))
    baseline_y = centre[1] + round(9 * unit)
    origin = (centre[0] + radius + round(12 * unit), baseline_y)
    # A dark outline keeps the label readable over bright backgrounds.
    cv2.putText(
        out, text, origin, cv2.FONT_HERSHEY_DUPLEX, scale, (0, 0, 0), thick + 2, cv2.LINE_AA
    )
    cv2.putText(out, text, origin, cv2.FONT_HERSHEY_DUPLEX, scale, _WHITE, thick, cv2.LINE_AA)
    return out


class OpenCvRecordingUI:
    """``RecordingUI`` backed by an OpenCV window."""

    def __init__(self, window: str = RECORD_WINDOW) -> None:
        """Create the window (shown on the first frame)."""
        self.window = window
        self._last_key: int = -1
        self._shown = False
        cv2.namedWindow(self.window, cv2.WINDOW_AUTOSIZE)

    def show(self, frame: Frame, elapsed_s: float) -> None:
        """Show one preview frame with the REC mark and pump window events."""
        cv2.imshow(self.window, draw_rec_overlay(frame, elapsed_s))
        self._shown = True
        # waitKey both pumps the window's messages and reads a key; 1 ms keeps up with 30 fps.
        self._last_key = int(cv2.waitKey(1))

    def poll_key(self) -> str | None:
        """Return the action for the last key pressed, once."""
        action = key_action(self._last_key)
        self._last_key = -1
        return action

    def window_closed(self) -> bool:
        """Return True once the user has closed the window with its close button."""
        if not self._shown:
            return False
        try:
            return float(cv2.getWindowProperty(self.window, cv2.WND_PROP_VISIBLE)) < 1
        except cv2.error:
            return True

    def close(self) -> None:
        """Close the window if it is still open."""
        try:
            cv2.destroyWindow(self.window)
            cv2.waitKey(1)
        except cv2.error:
            pass


PLAYBACK_WINDOW = "Teams Avatar - preview (Esc: close)"


class OpenCvPlaybackUI:
    """``PlaybackUI`` for ``preview``: shows frames in an OpenCV window; Esc closes it."""

    def __init__(self, window: str = PLAYBACK_WINDOW) -> None:
        """Create the window (shown on the first frame)."""
        self.window = window
        self._last_key: int = -1
        self._shown = False
        cv2.namedWindow(self.window, cv2.WINDOW_AUTOSIZE)

    def show(self, frame: Frame) -> None:
        """Show one frame and pump window events."""
        cv2.imshow(self.window, frame)
        self._shown = True
        self._last_key = int(cv2.waitKey(1))

    def poll_key(self) -> str | None:
        """Return ``"cancel"`` after Esc, once."""
        action = "cancel" if key_action(self._last_key) == "cancel" else None
        self._last_key = -1
        return action

    def window_closed(self) -> bool:
        """Return True once the user has closed the window."""
        if not self._shown:
            return False
        try:
            return float(cv2.getWindowProperty(self.window, cv2.WND_PROP_VISIBLE)) < 1
        except cv2.error:
            return True

    def close(self) -> None:
        """Close the window if it is still open."""
        try:
            cv2.destroyWindow(self.window)
            cv2.waitKey(1)
        except cv2.error:
            pass
