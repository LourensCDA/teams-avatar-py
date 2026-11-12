"""PyAV live-camera adapter: DirectShow capture with a mode ladder (research R2).

This is the only module allowed to open a physical camera; the camera service must never
import it, so a recorded video can never be swapped for the live feed.

Every device call that can stall (open, read) runs off the caller's thread with a deadline, and
every opened stream is closed on every path, so a bad driver cannot hang a command while
holding the camera.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Protocol

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
DARK_MEAN = 30.0
_MAX_MEASURE_REAL_S = 2.5
_OBS_NAME = "obs virtual camera"
_LIGHT_STEP = "Add light or open the camera shutter, then try again."
_NO_OPEN = (
    "it is unusable for recording: it is in use by another program, blocked in Windows "
    "camera privacy settings, or disconnected"
)


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
    """An opened camera stream in one mode.

    A stream may also expose ``last_timestamp`` (seconds of the last frame) and ``mjpeg``
    (whether MJPEG was really delivered); both are optional and read with ``getattr``.
    """

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
        self._frames: Iterator[av.VideoFrame] = container.decode(video=0)
        self.last_timestamp: float | None = None
        self.mjpeg = container.streams.video[0].codec_context.name == "mjpeg"

    def read(self) -> Frame:
        """Decode the next frame to BGR (yuyv422 and MJPEG are both converted here)."""
        frame = next(self._frames)
        # Driver timestamps survive buffering, so rates measured from them are not inflated
        # by frames that queued up while the stream was being opened.
        if frame.pts is not None and frame.time_base is not None:
            self.last_timestamp = float(frame.pts * frame.time_base)
        else:
            self.last_timestamp = None
        out: Frame = np.asarray(frame.to_ndarray(format="bgr24"), dtype=np.uint8)
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


def _safe_close(stream: FrameStream) -> None:
    """Close a stream, swallowing errors: a dead device may fail to close cleanly."""
    try:
        stream.close()
    except Exception:  # noqa: BLE001, S110 - nothing useful can be done about a failed close
        pass


class _TimedStream:
    """Runs a stream's blocking ``read`` on a helper thread so each read has a deadline."""

    def __init__(self, inner: FrameStream, read_timeout_s: float) -> None:
        """Start the reader thread for ``inner``."""
        self._inner = inner
        self._timeout_s = read_timeout_s
        self._requests: queue.Queue[bool] = queue.Queue()
        self._results: queue.Queue[tuple[Frame | None, BaseException | None]] = queue.Queue()
        self._closed = False
        self._reading = False  # a read is in flight on the helper thread
        self._lock = threading.Lock()
        threading.Thread(target=self._loop, name="camera-read", daemon=True).start()

    @property
    def last_timestamp(self) -> float | None:
        """Return the inner stream's last frame timestamp, if it provides one."""
        value: float | None = getattr(self._inner, "last_timestamp", None)
        return value

    @property
    def mjpeg(self) -> bool | None:
        """Return whether MJPEG was delivered, if the inner stream knows."""
        value: bool | None = getattr(self._inner, "mjpeg", None)
        return value

    def _loop(self) -> None:
        """Serve read requests until closed.

        If the stream was abandoned while a read was blocked, this thread closes it once the
        read returns: PyAV containers must not be closed under a decode in progress.
        """
        while self._requests.get():
            result: tuple[Frame | None, BaseException | None]
            try:
                result = (self._inner.read(), None)
            except BaseException as exc:  # noqa: BLE001 - re-raised in the caller's thread
                result = (None, exc)
            with self._lock:
                self._reading = False
                abandoned = self._closed
            if abandoned:
                _safe_close(self._inner)
                return
            self._results.put(result)

    def read(self, timeout_s: float | None = None) -> Frame:
        """Return the next frame; on a stall abandon the stream and raise ``TimeoutError``."""
        with self._lock:
            if self._closed:
                raise OSError("stream is closed")
            # Queued under the lock: a close() then either sees no read in flight (and closes
            # the stream itself) or finds this request ahead of its stop signal.
            self._reading = True
            self._requests.put(True)
        try:
            frame, exc = self._results.get(timeout=timeout_s or self._timeout_s)
        except queue.Empty:
            self.close()
            raise TimeoutError("the camera stopped delivering frames") from None
        if exc is not None:
            raise exc
        if frame is None:
            raise OSError("no frame")
        return frame

    def close(self) -> None:
        """Close the inner stream and stop the thread; idempotent and never raises.

        While a read is still blocked the stream is only marked abandoned and the reader thread
        closes it when that read returns, so no thread closes it under another's decode.
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            in_flight = self._reading
        self._requests.put(False)
        if not in_flight:
            _safe_close(self._inner)


@dataclass(frozen=True)
class _Measure:
    """Result of timing one opened mode."""

    fps: float
    width: int
    height: int
    dark: bool
    mjpeg: bool | None


class _Attempt:
    """Hand-off between the ladder thread and the waiting caller, safe against late results."""

    def __init__(self) -> None:
        """Create an attempt that is still wanted."""
        self.lock = threading.Lock()
        self.done = threading.Event()
        self.abandoned = False
        self.stream: _TimedStream | None = None
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

    def publish(self, stream: _TimedStream, mode: Mode, size: tuple[int, int]) -> None:
        """Offer a good stream; close it at once if the caller already gave up."""
        with self.lock:
            if not self.abandoned:
                self.stream, self.mode, self.size = stream, mode, size
                return
        stream.close()


def _close_to(a: float, b: float) -> bool:
    """Return whether two low rates are about the same (a lower resolution will not help)."""
    return abs(a - b) <= max(1.5, 0.2 * max(a, b))


class CameraSource:
    """Records from a physical camera, choosing the best mode that really delivers 27+ fps."""

    def __init__(
        self,
        open_device: OpenDevice = open_dshow,
        clock: Callable[[], float] = time.monotonic,
        open_timeout_s: float = 10,
        ladder: list[Mode] | None = None,
        read_timeout_s: float = 3.0,
    ) -> None:
        """Configure the opener, clock, whole-open timeout, mode ladder and per-read deadline."""
        self._open_device = open_device
        self._clock = clock
        self.open_timeout_s = open_timeout_s
        self._ladder = ladder if ladder is not None else DEFAULT_LADDER
        self._read_timeout_s = read_timeout_s
        self._stream: _TimedStream | None = None
        self._camera_name = ""
        self.mode: Mode | None = None
        self._size: tuple[int, int] | None = None

    @property
    def size(self) -> tuple[int, int]:
        """Return the opened capture size as (width, height).

        Raises:
            CameraUnavailableError: If the camera is not open.
        """
        if self._size is None:
            raise CameraUnavailableError(self._camera_name or "camera", "it is not open")
        return self._size

    def open(self, camera: CameraInfo) -> None:
        """Pick the first ladder mode that opens and sustains the target rate.

        Raises:
            CameraUnavailableError: If the camera is the OBS Virtual Camera, delivers too few
                frames per second, cannot be opened in any mode, or the whole attempt exceeds
                the timeout.
        """
        self.close()
        name = camera.name
        if _OBS_NAME in name.lower() or _OBS_NAME in camera.device_name.lower():
            raise CameraUnavailableError(
                name, "it is this tool's own output; recording it would feed back on itself"
            )
        attempt = _Attempt()
        # The ladder stops starting modes at 80% of the budget so its own, more specific reason
        # normally arrives before this outer timeout fires.
        ladder_deadline = time.monotonic() + 0.8 * self.open_timeout_s
        # Daemon: a driver call that never returns must not keep the process alive.
        worker = threading.Thread(
            target=self._run_ladder,
            args=(camera, attempt, ladder_deadline),
            name="camera-open",
            daemon=True,
        )
        worker.start()
        if not attempt.done.wait(self.open_timeout_s):
            attempt.abandon()
            raise CameraUnavailableError(
                name,
                f"it did not open within {self.open_timeout_s:g} seconds",
                "Unplug and replug the camera or pick another one, then try again.",
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
            CameraUnavailableError: If the camera is not open or stops delivering frames; the
                device is released in that case.
        """
        stream = self._stream
        if stream is None:
            raise CameraUnavailableError(self._camera_name or "camera", "it is not open")
        try:
            return stream.read()
        except Exception as exc:  # unplugged mid-recording surfaces as many PyAV types
            self.close()
            raise CameraUnavailableError(
                self._camera_name, "it stopped delivering frames (was it unplugged?)"
            ) from exc

    def close(self) -> None:
        """Release the camera; safe to call repeatedly and never raises."""
        stream, self._stream = self._stream, None
        self.mode = None
        self._size = None
        if stream is not None:
            stream.close()

    def _run_ladder(self, camera: CameraInfo, attempt: _Attempt, deadline: float) -> None:
        """Worker body: try modes, publish the first good stream or a specific error."""
        name = camera.name
        measured: list[_Measure] = []
        try:
            for mode in self._ladder:
                if attempt.abandoned or time.monotonic() >= deadline:
                    break
                try:
                    raw = self._open_device(camera, mode, TARGET_FPS)
                except Exception:  # noqa: BLE001 - any failure just means "next mode"
                    continue
                stream = _TimedStream(raw, self._read_timeout_s)
                if attempt.abandoned:  # abandoned while the device was opening
                    stream.close()
                    break
                result = self._measure(stream, attempt, deadline)
                if result is None or result.fps < MIN_FPS:
                    stream.close()
                    if result is not None:
                        measured.append(result)
                        lows = [m.fps for m in measured[-2:]]
                        if len(lows) == 2 and _close_to(lows[0], lows[1]):
                            break  # same low rate twice: a lower resolution will not help
                    continue
                mjpeg = mode.mjpeg if result.mjpeg is None else result.mjpeg
                actual = Mode(result.width, result.height, mjpeg)
                attempt.publish(stream, actual, (result.width, result.height))
                return
            if not attempt.abandoned:
                attempt.error = self._failure(name, measured)
        finally:
            attempt.done.set()

    @staticmethod
    def _failure(name: str, measured: list[_Measure]) -> CameraUnavailableError:
        """Build the most specific error for a ladder that found no usable mode."""
        if not measured:
            return CameraUnavailableError(name, _NO_OPEN)
        best = max(measured, key=lambda m: m.fps)
        reason = f"it is unusable for recording: it only delivers {best.fps:.0f} frames per second"
        if best.dark:
            return CameraUnavailableError(
                name,
                reason + ", usually because the room is too dark or the camera shutter is closed",
                _LIGHT_STEP,
            )
        return CameraUnavailableError(
            name, reason, 'Try better light or pick another with "teams-avatar cameras".'
        )

    def _measure(self, stream: _TimedStream, attempt: _Attempt, deadline: float) -> _Measure | None:
        """Time one second of frames from their timestamps; None if reads fail or stall."""
        stamps: list[float] = []
        first: Frame | None = None
        last: Frame | None = None
        stop = time.monotonic() + _MAX_MEASURE_REAL_S
        try:
            while True:
                if attempt.abandoned:
                    return None
                left = min(stop, deadline + 0.2 * self.open_timeout_s) - time.monotonic()
                if stamps and left <= 0:
                    break
                last = stream.read(max(0.2, min(self._read_timeout_s, left)))
                first = first if first is not None else last
                stamp = stream.last_timestamp
                stamps.append(stamp if stamp is not None else self._clock())
                if stamps[-1] - stamps[0] >= MEASURE_S:
                    break
        except Exception:  # noqa: BLE001 - stalled or failing reads reject the mode
            return None
        if first is None or last is None or len(stamps) < 2:
            return None
        span = stamps[-1] - stamps[0]
        fps = (len(stamps) - 1) / span if span > 0 else 0.0
        brightness = (float(first[::8, ::8].mean()) + float(last[::8, ::8].mean())) / 2
        return _Measure(
            fps, int(first.shape[1]), int(first.shape[0]), brightness < DARK_MEAN, stream.mjpeg
        )
