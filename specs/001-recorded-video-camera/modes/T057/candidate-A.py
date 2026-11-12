"""Physical camera capture through PyAV and DirectShow, with a capture mode ladder (R2).

This is the only module allowed to open a live camera; the camera service must never import it.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Protocol

import av
import numpy as np
import numpy.typing as npt

from teams_avatar.errors import CameraUnavailableError
from teams_avatar.models import CameraInfo

Frame = npt.NDArray[np.uint8]

TARGET_FPS = 30
MIN_FPS = 27.0
MEASURE_SECONDS = 1.0
OPEN_TIMEOUT_S = 10.0
_OBS_NAME = "obs virtual camera"


@dataclass(frozen=True)
class Mode:
    """One DirectShow capture mode to try."""

    width: int
    height: int
    mjpeg: bool


# DirectShow cannot list modes, so they are tried best-first and the first usable one is kept.
DEFAULT_LADDER: list[Mode] = [
    Mode(1920, 1080, mjpeg=True),
    Mode(1920, 1080, mjpeg=False),
    Mode(1280, 720, mjpeg=True),
    Mode(1280, 720, mjpeg=False),
    Mode(960, 540, mjpeg=False),
    Mode(640, 480, mjpeg=False),
]


class FrameSource(Protocol):
    """What the recording session needs from a camera (shared by the real source and fakes)."""

    @property
    def size(self) -> tuple[int, int]:
        """Return the opened capture size as (width, height)."""
        ...

    def open(self, camera: CameraInfo) -> None:
        """Open ``camera`` or raise ``CameraUnavailableError``."""

    def read(self) -> Frame:
        """Return the next BGR frame."""
        ...

    def close(self) -> None:
        """Release the camera."""


class Stream(Protocol):
    """An opened capture stream."""

    def read(self) -> Frame:
        """Return the next BGR frame."""
        ...

    def close(self) -> None:
        """Release the device."""


OpenDevice = Callable[[CameraInfo, Mode, int], Stream]


class _AvStream:
    """A PyAV DirectShow container yielding BGR frames."""

    def __init__(self, container: av.container.InputContainer) -> None:
        """Wrap an opened container."""
        self._container = container
        self._frames: Iterator[Any] = container.decode(video=0)

    def read(self) -> Frame:
        """Decode the next frame to BGR; end of stream means the device went away."""
        frame = next(self._frames)
        out: Frame = frame.to_ndarray(format="bgr24")
        return out

    def close(self) -> None:
        """Close the container."""
        self._container.close()


def open_dshow(camera: CameraInfo, mode: Mode, fps: int) -> Stream:
    """Open ``camera`` by DirectShow name in ``mode`` (the real device opener).

    Raises:
        OSError: If the device or mode is refused (PyAV errors are OSError subclasses).
    """
    options = {
        "video_size": f"{mode.width}x{mode.height}",
        "framerate": str(fps),
        "rtbufsize": "128M",
    }
    if mode.mjpeg:
        options["vcodec"] = "mjpeg"
    if camera.device_number:
        # Disambiguates identically named cameras.
        options["video_device_number"] = str(camera.device_number)
    container = av.open(f"video={camera.device_name}", format="dshow", options=options)
    return _AvStream(container)


class _Attempt:
    """Shared state between ``open`` and its worker thread, guarded by a lock."""

    def __init__(self) -> None:
        """Start with nothing opened and not abandoned."""
        self.lock = threading.Lock()
        self.abandoned = False
        self.stream: Stream | None = None
        self.mode: Mode | None = None
        self.reason = ""


class CameraSource:
    """Records from a physical camera, choosing the best mode that sustains the frame rate."""

    def __init__(
        self,
        open_device: OpenDevice = open_dshow,
        clock: Callable[[], float] = time.monotonic,
        open_timeout_s: float = OPEN_TIMEOUT_S,
    ) -> None:
        """Create a closed source; ``open_device`` and ``clock`` are injectable for tests."""
        self._open_device = open_device
        self._clock = clock
        self.open_timeout_s = open_timeout_s
        self._stream: Stream | None = None
        self._camera_name = ""
        self.mode: Mode | None = None

    @property
    def size(self) -> tuple[int, int]:
        """Return the opened capture size as (width, height)."""
        if self.mode is None:
            raise CameraUnavailableError(self._camera_name or "camera", "it is not open")
        return self.mode.width, self.mode.height

    def open(self, camera: CameraInfo) -> None:
        """Open ``camera`` in the best working mode within the open timeout.

        Raises:
            CameraUnavailableError: If the camera is OBS Virtual Camera, in use, unusable for
                recording, or does not open within the timeout.
        """
        self.close()
        name = camera.name
        if _OBS_NAME in name.lower() or _OBS_NAME in camera.device_name.lower():
            raise CameraUnavailableError(
                name, "it is the tool's own output and would feed back into the recording"
            )
        attempt = _Attempt()
        worker = threading.Thread(target=self._run_ladder, args=(camera, attempt), daemon=True)
        worker.start()
        worker.join(self.open_timeout_s)
        with attempt.lock:
            if worker.is_alive():
                # Why: the worker may still be blocked in the driver; it closes whatever it
                # opens once it sees this flag, so a late stream never keeps the camera.
                attempt.abandoned = True
                raise CameraUnavailableError(
                    name, f"it did not open within {self.open_timeout_s:g} s (in use elsewhere?)"
                )
            stream, mode = attempt.stream, attempt.mode
        if stream is None or mode is None:
            raise CameraUnavailableError(name, attempt.reason or "it could not be opened")
        self._stream, self.mode, self._camera_name = stream, mode, name

    def _run_ladder(self, camera: CameraInfo, attempt: _Attempt) -> None:
        """Worker: try each mode and publish the first good stream unless abandoned."""
        last_error: BaseException | None = None
        opened_any = False
        try:
            for mode in DEFAULT_LADDER:
                if attempt.abandoned:
                    return
                try:
                    stream = self._open_device(camera, mode, TARGET_FPS)
                except Exception as exc:
                    last_error = exc
                    continue
                opened_any = True
                try:
                    fast = self._sustains_rate(stream)
                except Exception as exc:
                    last_error = exc
                    fast = False
                if not fast:
                    _close_quietly(stream)
                    continue
                with attempt.lock:
                    if attempt.abandoned:
                        _close_quietly(stream)
                        return
                    attempt.stream, attempt.mode = stream, mode
                return
            if opened_any:
                attempt.reason = (
                    f"it is unusable for recording (no mode reached {MIN_FPS:g} frames per second)"
                )
            else:
                detail = f": {last_error}" if last_error else ""
                attempt.reason = (
                    "it is unusable for recording (no capture mode opened; it may be in use "
                    f"by another program){detail}"
                )
        except BaseException as exc:  # a dying worker must still explain itself
            attempt.reason = f"it is unusable for recording ({exc})"

    def _sustains_rate(self, stream: Stream) -> bool:
        """Read for one second of clock time and check the delivered frame rate."""
        start = self._clock()
        frames = 0
        while True:
            stream.read()
            frames += 1
            elapsed = self._clock() - start
            if elapsed >= MEASURE_SECONDS:
                return frames / elapsed >= MIN_FPS

    def read(self) -> Frame:
        """Return the next BGR frame.

        Raises:
            CameraUnavailableError: If the camera is closed or stops delivering (unplugged).
        """
        if self._stream is None:
            raise CameraUnavailableError(self._camera_name or "camera", "it is not open")
        try:
            return self._stream.read()
        except CameraUnavailableError:
            raise
        except Exception as exc:
            raise CameraUnavailableError(
                self._camera_name, "it stopped delivering frames (unplugged?)"
            ) from exc

    def close(self) -> None:
        """Release the camera; safe to call repeatedly."""
        stream, self._stream, self.mode = self._stream, None, None
        if stream is not None:
            _close_quietly(stream)


def _close_quietly(stream: Stream) -> None:
    """Close a stream, ignoring errors from a device that is already gone."""
    try:
        stream.close()
    except Exception:  # best-effort release of a possibly dead device
        return
