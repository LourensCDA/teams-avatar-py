"""Frame sink that drives "OBS Virtual Camera" through pyvirtualcam's ``obs`` backend (R1)."""

from __future__ import annotations

import winreg
from typing import Any, Protocol

import cv2
import numpy as np
import numpy.typing as npt
import pyvirtualcam

from teams_avatar.errors import VirtualCameraError

Frame = npt.NDArray[np.uint8]

# COM class OBS Studio registers for its DirectShow virtual camera.
OBS_VCAM_CLSID = "{A3FCE0F5-3493-419F-958A-ABA1250EC20B}"

# pyvirtualcam's messages, probed on the development machine (research R1).
_NOT_INSTALLED_HINT = "did you install obs"
_IN_USE_HINT = "could not be started"


class FrameSink(Protocol):
    """Where the camera service sends one frame per tick."""

    def open(self, width: int, height: int, fps: float) -> None:
        """Start sending at this size and rate."""

    def send(self, frame: Frame) -> None:
        """Send one BGR frame of the opened size."""

    def sleep_until_next_frame(self) -> None:
        """Block until the next frame is due."""

    def close(self) -> None:
        """Release the device."""


def obs_installed() -> bool:
    """Return True if OBS Studio's virtual camera is registered with Windows."""
    for root in (winreg.HKEY_CLASSES_ROOT, winreg.HKEY_LOCAL_MACHINE):
        sub = rf"CLSID\{OBS_VCAM_CLSID}"
        if root == winreg.HKEY_LOCAL_MACHINE:
            sub = rf"SOFTWARE\Classes\CLSID\{OBS_VCAM_CLSID}"
        try:
            with winreg.OpenKey(root, sub):
                return True
        except OSError:
            continue
    return False


def map_backend_error(exc: BaseException) -> VirtualCameraError | None:
    """Translate a pyvirtualcam error into ``VirtualCameraError``, or None if unrecognised."""
    text = str(exc).lower()
    if _NOT_INSTALLED_HINT in text or "device not found" in text:
        return VirtualCameraError("not_installed")
    if _IN_USE_HINT in text:
        return VirtualCameraError("in_use")
    return None


class ObsFrameSink:
    """``FrameSink`` over pyvirtualcam; pacing comes from pyvirtualcam's own frame timer."""

    def __init__(self) -> None:
        """Create a closed sink."""
        self._camera: Any = None

    def open(self, width: int, height: int, fps: float) -> None:
        """Open OBS Virtual Camera at ``width`` x ``height`` and ``fps``.

        Raises:
            VirtualCameraError: ``not_installed`` if OBS is missing, ``in_use`` if another
                program (for example OBS's own "Start Virtual Camera") already drives it.
        """
        if not obs_installed():
            raise VirtualCameraError("not_installed")
        # OpenCV's worker threads spin between calls and cost ~8 CPU ms per 720p conversion
        # against ~1 ms single-threaded; this is process-wide on purpose.
        cv2.setNumThreads(1)
        try:
            self._camera = pyvirtualcam.Camera(
                width=width,
                height=height,
                fps=fps,
                backend="obs",
                fmt=pyvirtualcam.PixelFormat.I420,
            )
        except RuntimeError as exc:
            mapped = map_backend_error(exc)
            if mapped is None:
                raise
            raise mapped from exc

    def send(self, frame: Frame) -> None:
        """Send one BGR frame, converted here to planar I420.

        pyvirtualcam turns I420 into the device's NV12 with a plain plane copy, which costs about
        a third of its BGR conversion and of an interleave done in numpy.
        """
        if self._camera is None:
            raise RuntimeError("the virtual camera is not open")
        self._camera.send(cv2.cvtColor(frame, cv2.COLOR_BGR2YUV_I420))

    def sleep_until_next_frame(self) -> None:
        """Wait until the next frame is due at the opened rate."""
        if self._camera is not None:
            self._camera.sleep_until_next_frame()

    def close(self) -> None:
        """Release OBS Virtual Camera so another program can use it."""
        if self._camera is not None:
            self._camera.close()
            self._camera = None
