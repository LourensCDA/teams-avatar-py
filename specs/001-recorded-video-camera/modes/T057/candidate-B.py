"""PyAV/DirectShow live-camera capture with a mode ladder (research R2).

This is the only module allowed to open a physical camera; the camera service must never import
it. DirectShow cannot list a device's modes, so the ladder is tried in order and the first mode
that opens *and* sustains the target frame rate wins.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import av
import av.error
import numpy as np
import numpy.typing as npt

from teams_avatar.errors import CameraUnavailableError
from teams_avatar.models import CameraInfo

Frame = npt.NDArray[np.uint8]

TARGET_FPS = 30
MIN_FPS = 27.0
_MEASURE_S = 1.0
_OBS_CAMERA_NAME = "obs virtual camera"
_BUSY_HINTS = ("permission denied", "in use", "busy", "access is denied", "could not run graph")
_AV_ERRORS = (av.error.FFmpegError, OSError, ValueError, EOFError)


@dataclass(frozen=True)
class Mode:
    """A requested capture mode: frame size and whether the camera is asked for MJPEG."""

    width: int
    height: int
    mjpeg: bool


DEFAULT_LADDER: list[Mode] = [
    Mode(1920, 1080, mjpeg=True),
    Mode(1920, 1080, mjpeg=False),
    Mode(1280, 720, mjpeg=True),
    Mode(1280, 720, mjpeg=False),
    Mode(960, 540, mjpeg=False),
    Mode(640, 480, mjpeg=False),
]


class FrameStream(Protocol):
    """An opened device: blocking frame reads and release."""

    def read(self) -> Frame:
        """Return the next BGR frame, raising on failure."""
        ...

    def close(self) -> None:
        """Release the device."""
        ...


class FrameSource(Protocol):
    """What a recording session needs from a camera (shared by ``CameraSource`` and fakes)."""

    @property
    def size(self) -> tuple[int, int]:
        """Return the opened ``(width, height)``."""
        ...

    def open(self, camera: CameraInfo) -> None:
        """Open ``camera`` or raise ``CameraUnavailableError``."""
        ...

    def read(self) -> Frame:
        """Return the next BGR frame."""
        ...

    def close(self) -> None:
        """Release the camera."""
        ...


OpenDevice = Callable[[CameraInfo, Mode, int], FrameStream]


class _PyAvStream:
    """``FrameStream`` over a PyAV DirectShow container."""

    def __init__(self, container: av.container.InputContainer) -> None:
        """Wrap an open container and start its decode generator."""
        self._container = container
        self._frames = container.decode(video=0)

    def read(self) -> Frame:
        """Decode the next frame to BGR."""
        frame: Frame = np.asarray(next(self._frames).to_ndarray(format="bgr24"), dtype=np.uint8)
        return frame

    def close(self) -> None:
        """Close the container, ignoring errors from an already-dead device."""
        try:
            self._container.close()
        except _AV_ERRORS:
            pass


def open_dshow(camera: CameraInfo, mode: Mode, fps: int) -> FrameStream:
    """Open ``camera`` through DirectShow in ``mode``.

    Args:
        camera: Camera to open (by device name; ``device_number`` disambiguates duplicates).
        mode: Requested size and codec.
        fps: Requested frame rate.

    Returns:
        An open stream.

    Raises:
        OSError: If the device refuses the mode or is unavailable.
    """
    options = {
        "video_size": f"{mode.width}x{mode.height}",
        "framerate": str(fps),
        # Large buffer so a slow first read does not drop frames at 1080p.
        "rtbufsize": "256M",
    }
    if mode.mjpeg:
        options["vcodec"] = "mjpeg"
    if camera.device_number:
        options["video_device_number"] = str(camera.device_number)
    try:
        container = av.open(f"video={camera.device_name}", format="dshow", options=options)
    except _AV_ERRORS as exc:
        raise OSError(str(exc)) from exc
    return _PyAvStream(container)


class _Attempt:
    """State shared between ``open`` and its worker thread."""

    def __init__(self) -> None:
        """Create an attempt that has not finished or been abandoned."""
        self.lock = threading.Lock()
        self.abandoned = False
        self.result: tuple[FrameStream, Mode] | None = None
        self.error: CameraUnavailableError | None = None
        self.done = threading.Event()


class CameraSource:
    """Physical camera recorded through the capture mode ladder."""

    def __init__(
        self,
        open_device: OpenDevice = open_dshow,
        clock: Callable[[], float] = time.monotonic,
        open_timeout_s: float = 10,
        ladder: list[Mode] | None = None,
    ) -> None:
        """Create a closed source; ``open_device`` and ``clock`` are injectable for tests."""
        self._open_device = open_device
        self._clock = clock
        self.open_timeout_s = open_timeout_s
        self._ladder = ladder if ladder is not None else DEFAULT_LADDER
        self._stream: FrameStream | None = None
        self._mode: Mode | None = None
        self._name = ""

    @property
    def mode(self) -> Mode | None:
        """Return the accepted mode, or ``None`` while closed."""
        return self._mode

    @property
    def size(self) -> tuple[int, int]:
        """Return the opened ``(width, height)``."""
        if self._mode is None:
            raise RuntimeError("camera is not open")
        return self._mode.width, self._mode.height

    def open(self, camera: CameraInfo) -> None:
        """Open ``camera`` on the best mode that holds the frame rate.

        Raises:
            CameraUnavailableError: If it is the OBS virtual camera, no mode works, it is in
                use, or opening takes longer than ``open_timeout_s``.
        """
        self.close()
        if _OBS_CAMERA_NAME in (camera.name.lower(), camera.device_name.lower()):
            raise CameraUnavailableError(
                camera.name, "it is this tool's own output, so recording it would feed back"
            )
        attempt = _Attempt()
        worker = threading.Thread(
            target=self._run_ladder, args=(camera, attempt), name="camera-open", daemon=True
        )
        worker.start()
        finished = attempt.done.wait(self.open_timeout_s)
        with attempt.lock:
            if not finished and not attempt.done.is_set():
                # The worker closes any stream it opens after this point.
                attempt.abandoned = True
                raise CameraUnavailableError(
                    camera.name, f"it did not open within {self.open_timeout_s:g} seconds"
                )
            if attempt.error is not None:
                raise attempt.error
            if attempt.result is None:
                raise CameraUnavailableError(camera.name, "it could not be opened")
            self._stream, self._mode = attempt.result
            self._name = camera.name

    def read(self) -> Frame:
        """Return the next BGR frame.

        Raises:
            CameraUnavailableError: If the camera fails (for example, it was unplugged).
        """
        if self._stream is None:
            raise RuntimeError("camera is not open")
        try:
            return self._stream.read()
        except (*_AV_ERRORS, StopIteration) as exc:
            raise CameraUnavailableError(self._name, "it stopped delivering frames") from exc

    def close(self) -> None:
        """Release the camera; safe to call repeatedly."""
        stream, self._stream, self._mode = self._stream, None, None
        if stream is not None:
            stream.close()

    def _run_ladder(self, camera: CameraInfo, attempt: _Attempt) -> None:
        """Worker: find the first good mode and publish it (or the error) on ``attempt``."""
        try:
            found = self._search(camera, attempt)
            if found is None:
                return
            with attempt.lock:
                if not attempt.abandoned:
                    attempt.result = found
                    return
            found[0].close()  # the caller gave up waiting; do not leak the device
        except CameraUnavailableError as exc:
            attempt.error = exc
        finally:
            attempt.done.set()

    def _search(self, camera: CameraInfo, attempt: _Attempt) -> tuple[FrameStream, Mode] | None:
        """Try each mode in order; return the first stream that sustains ``MIN_FPS``."""
        busy = False
        for mode in self._ladder:
            if attempt.abandoned:
                return None
            try:
                stream = self._open_device(camera, mode, TARGET_FPS)
            except _AV_ERRORS as exc:
                if any(hint in str(exc).lower() for hint in _BUSY_HINTS):
                    busy = True
                    break  # every mode would hit the same lock
                continue
            if attempt.abandoned:
                stream.close()
                return None
            if self._sustains_rate(stream, attempt):
                return stream, mode
            stream.close()
        if busy:
            raise CameraUnavailableError(camera.name, "it is in use by another program")
        raise CameraUnavailableError(
            camera.name, "it is unusable for recording (no supported mode reaches 27 fps)"
        )

    def _sustains_rate(self, stream: FrameStream, attempt: _Attempt) -> bool:
        """Read for about one second and report whether the rate is at least ``MIN_FPS``."""
        start = self._clock()
        frames = 0
        try:
            while not attempt.abandoned:
                stream.read()
                frames += 1
                elapsed = self._clock() - start
                if elapsed >= _MEASURE_S:
                    return frames / elapsed >= MIN_FPS
        except (*_AV_ERRORS, StopIteration):
            return False
        return False
