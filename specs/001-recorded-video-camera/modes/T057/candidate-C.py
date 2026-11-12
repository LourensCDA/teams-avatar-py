"""PyAV live-camera adapter: DirectShow capture with a mode ladder (research R2).

This is the only module allowed to open a physical camera; the camera service must never
import it, so a recorded video can never be swapped for the live feed.
"""

from __future__ import annotations

import errno
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Protocol

import av
import av.container
import numpy as np
import numpy.typing as npt

from teams_avatar.errors import CameraUnavailableError
from teams_avatar.models import CameraInfo

Frame = npt.NDArray[np.uint8]

TARGET_FPS = 30
MIN_FPS = 27.0
MEASURE_S = 1.0
_OBS_NAME = "obs virtual camera"
_IN_USE_HINTS = ("in use", "busy", "already", "could not run graph")


@dataclass(frozen=True)
class Mode:
    """One capture mode to try: frame size and whether to ask the camera for MJPEG."""

    width: int
    height: int
    mjpeg: bool


# Modes cannot be listed from DirectShow through ffmpeg, so they are tried best-first.
DEFAULT_LADDER: list[Mode] = [
    Mode(1920, 1080, mjpeg=True),
    Mode(1920, 1080, mjpeg=False),
    Mode(1280, 720, mjpeg=True),
    Mode(1280, 720, mjpeg=False),
    Mode(960, 540, mjpeg=False),
    Mode(640, 480, mjpeg=False),
]


class FrameStream(Protocol):
    """An opened camera stream in one mode."""

    def read(self) -> Frame:
        """Return the next BGR frame."""
        ...

    def close(self) -> None:
        """Release the device."""
        ...


class FrameSource(Protocol):
    """What the recording session needs from a camera; shared by ``CameraSource`` and fakes."""

    @property
    def size(self) -> tuple[int, int]:
        """Return the opened capture size as (width, height)."""
        ...

    def open(self, camera: CameraInfo) -> None:
        """Open ``camera`` or raise ``CameraUnavailableError``."""
        ...

    def read(self) -> Frame:
        """Return the next BGR frame or raise ``CameraUnavailableError``."""
        ...

    def close(self) -> None:
        """Release the camera; safe to call twice."""
        ...


OpenDevice = Callable[[CameraInfo, Mode, int], FrameStream]


class _PyAvStream:
    """``FrameStream`` over a PyAV DirectShow container."""

    def __init__(self, container: av.container.InputContainer) -> None:
        """Wrap an opened input container."""
        self._container = container
        self._frames: Iterator[Any] = container.decode(video=0)

    def read(self) -> Frame:
        """Decode the next frame to BGR (yuyv422 and MJPEG are both converted here)."""
        frame = next(self._frames)
        out: Frame = frame.to_ndarray(format="bgr24")
        return out

    def close(self) -> None:
        """Close the container, releasing the device."""
        self._container.close()


def open_dshow(camera: CameraInfo, mode: Mode, fps: int) -> FrameStream:
    """Open ``camera`` through DirectShow in ``mode``.

    Raises:
        OSError: If the device or mode cannot be opened (PyAV errors subclass OSError).
    """
    options = {
        "video_size": f"{mode.width}x{mode.height}",
        "framerate": str(fps),
        "rtbufsize": "256M",
    }
    if mode.mjpeg:
        options["vcodec"] = "mjpeg"
    if camera.device_number:
        options["video_device_number"] = str(camera.device_number)
    container = av.open(f"video={camera.device_name}", format="dshow", options=options, timeout=5.0)
    return _PyAvStream(container)


def _looks_in_use(exc: BaseException) -> bool:
    """Guess whether an open failure means another program holds the camera."""
    if isinstance(exc, OSError) and exc.errno in (errno.EBUSY, errno.EACCES):
        return True
    return any(hint in str(exc).lower() for hint in _IN_USE_HINTS)


class _Attempt:
    """Hand-off between the ladder thread and the waiting caller, safe against late results."""

    def __init__(self) -> None:
        """Create an attempt that is still wanted."""
        self.lock = threading.Lock()
        self.done = threading.Event()
        self.abandoned = False
        self.stream: FrameStream | None = None
        self.mode: Mode | None = None
        self.size: tuple[int, int] = (0, 0)
        self.error: CameraUnavailableError | None = None

    def abandon(self) -> None:
        """Mark the attempt as given up on and release any stream that already arrived."""
        with self.lock:
            self.abandoned = True
            stream, self.stream = self.stream, None
        if stream is not None:
            stream.close()

    def publish(self, stream: FrameStream, mode: Mode, size: tuple[int, int]) -> None:
        """Offer a good stream; close it at once if the caller already gave up."""
        with self.lock:
            if not self.abandoned:
                self.stream, self.mode, self.size = stream, mode, size
                return
        stream.close()


class CameraSource:
    """Records from a physical camera, choosing the best mode that really delivers 27+ fps."""

    def __init__(
        self,
        open_device: OpenDevice = open_dshow,
        clock: Callable[[], float] = time.monotonic,
        open_timeout_s: float = 10,
        ladder: list[Mode] | None = None,
    ) -> None:
        """Configure the opener, clock, whole-open timeout and mode ladder."""
        self._open_device = open_device
        self._clock = clock
        self.open_timeout_s = open_timeout_s
        self._ladder = ladder if ladder is not None else DEFAULT_LADDER
        self._stream: FrameStream | None = None
        self._camera_name = ""
        self.mode: Mode | None = None
        self._size = (0, 0)

    @property
    def size(self) -> tuple[int, int]:
        """Return the opened capture size as (width, height)."""
        return self._size

    def open(self, camera: CameraInfo) -> None:
        """Pick the first ladder mode that opens and sustains the target rate.

        Raises:
            CameraUnavailableError: If the camera is the OBS Virtual Camera, is in use, is
                unusable in every mode, or the whole attempt exceeds the timeout.
        """
        self.close()
        name = camera.name
        if _OBS_NAME in name.lower() or _OBS_NAME in camera.device_name.lower():
            raise CameraUnavailableError(
                name, "it is this tool's own output; recording it would feed back on itself"
            )
        attempt = _Attempt()
        # Daemon: a driver call that never returns must not keep the process alive.
        worker = threading.Thread(
            target=self._run_ladder, args=(camera, attempt), name="camera-open", daemon=True
        )
        worker.start()
        if not attempt.done.wait(self.open_timeout_s):
            attempt.abandon()
            raise CameraUnavailableError(
                name, f"it did not open within {self.open_timeout_s:g} seconds"
            )
        with attempt.lock:
            stream, mode, error = attempt.stream, attempt.mode, attempt.error
            attempt.stream = None
        if error is not None:
            if stream is not None:
                stream.close()
            raise error
        if stream is None or mode is None:
            raise CameraUnavailableError(name, "it did not open")
        self._stream, self.mode, self._size = stream, mode, attempt.size
        self._camera_name = name

    def read(self) -> Frame:
        """Return the next BGR frame.

        Raises:
            CameraUnavailableError: If the camera is not open or stops delivering frames.
        """
        if self._stream is None:
            raise CameraUnavailableError(self._camera_name or "camera", "it is not open")
        try:
            return self._stream.read()
        except Exception as exc:  # unplugged mid-recording surfaces as many PyAV types
            raise CameraUnavailableError(
                self._camera_name, "it stopped delivering frames (was it unplugged?)"
            ) from exc

    def close(self) -> None:
        """Release the camera; safe to call repeatedly."""
        stream, self._stream = self._stream, None
        self.mode = None
        if stream is not None:
            stream.close()

    def _run_ladder(self, camera: CameraInfo, attempt: _Attempt) -> None:
        """Worker body: try each mode and publish the first good stream or an error."""
        name = camera.name
        try:
            for mode in self._ladder:
                if attempt.abandoned:
                    return
                try:
                    stream = self._open_device(camera, mode, TARGET_FPS)
                except Exception as exc:
                    if _looks_in_use(exc):
                        attempt.error = CameraUnavailableError(name, "another program is using it")
                        return
                    continue
                size = self._measure(stream)
                if size is None:
                    stream.close()
                    continue
                attempt.publish(stream, mode, size)
                return
            attempt.error = CameraUnavailableError(
                name, "it is unusable for recording (no mode delivered 27 frames per second)"
            )
        finally:
            attempt.done.set()

    def _measure(self, stream: FrameStream) -> tuple[int, int] | None:
        """Read for one second and return the frame size if the rate is high enough."""
        try:
            first = stream.read()  # warm-up: the first frame can take long to arrive
            start = self._clock()
            frames = 0
            while (elapsed := self._clock() - start) < MEASURE_S:
                stream.read()
                frames += 1
        except Exception:
            return None
        if frames / elapsed < MIN_FPS:
            return None
        return int(first.shape[1]), int(first.shape[0])
