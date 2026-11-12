"""Fakes for device boundaries so unit tests run without camera, OBS or files (Principle II)."""

from __future__ import annotations

import threading
from collections.abc import Callable
from types import TracebackType
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt

if TYPE_CHECKING:
    from teams_avatar.models import CameraInfo

Frame = npt.NDArray[np.uint8]


class FakeVideoReader:
    """In-memory ``VideoReader``: frame ``i`` is a solid frame of value ``i % 256``.

    ``block_after`` makes ``read`` block (until ``release`` is set) once that many frames have
    been read, to simulate a stalled decoder; ``fail_after`` makes it raise instead.
    """

    def __init__(
        self,
        frame_count: int,
        width: int = 8,
        height: int = 8,
        fps: float = 30,
        block_after: int | None = None,
        fail_after: int | None = None,
    ) -> None:
        """Create a reader over ``frame_count`` synthetic frames."""
        self.frame_count = frame_count
        self.width = width
        self.height = height
        self.fps = fps
        self.position = 0
        self.seeks: list[int] = []
        self.reads: list[int] = []
        self.closed = False
        self.block_after = block_after
        self.fail_after = fail_after
        self.release = threading.Event()

    def read(self) -> Frame | None:
        """Return the next frame or None at the end."""
        if self.block_after is not None and len(self.reads) >= self.block_after:
            self.release.wait(timeout=5)
        if self.fail_after is not None and len(self.reads) >= self.fail_after:
            from teams_avatar.errors import UnreadableVideoError

            raise UnreadableVideoError("the file is damaged")
        if self.position >= self.frame_count:
            return None
        index = self.position
        self.position += 1
        self.reads.append(index)
        return np.full((self.height, self.width, 3), index % 256, dtype=np.uint8)

    def seek(self, index: int) -> None:
        """Make the next ``read`` return frame ``index``."""
        if not 0 <= index < self.frame_count:
            raise ValueError(index)
        self.seeks.append(index)
        self.position = index

    def close(self) -> None:
        """Mark the reader closed and unblock any waiting read."""
        self.closed = True
        self.release.set()

    def frame_time(self, index: int) -> float:
        """Return `index / fps`."""
        return index / self.fps

    def __enter__(self) -> FakeVideoReader:
        """Return self."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the reader."""
        self.close()


class FakeFrameSink:
    """``FrameSink`` that keeps every frame sent; pacing is a no-op."""

    def __init__(self) -> None:
        """Create a closed sink."""
        self.frames: list[Frame] = []
        self.size: tuple[int, int, float] | None = None
        self.closed = False
        self.sleeps = 0

    def open(self, width: int, height: int, fps: float) -> None:
        """Record the opened size."""
        self.size = (width, height, fps)

    def send(self, frame: Frame) -> None:
        """Keep a copy of the frame."""
        self.frames.append(np.array(frame, copy=True))

    def sleep_until_next_frame(self) -> None:
        """Count pacing calls without sleeping."""
        self.sleeps += 1

    def close(self) -> None:
        """Mark the sink closed."""
        self.closed = True


class FakeClock:
    """Monotonic clock under test control."""

    def __init__(self, start: float = 1000.0) -> None:
        """Start at ``start`` seconds."""
        self.now = start

    def __call__(self) -> float:
        """Return the current time."""
        return self.now

    def advance(self, seconds: float) -> None:
        """Move time forward."""
        self.now += seconds


class FakeProcess:
    """Process handle that reports ``exit_code`` (``None`` = still running)."""

    def __init__(self, exit_code: int | None = None) -> None:
        """Create a handle with a fixed exit code."""
        self.exit_code = exit_code

    def poll(self) -> int | None:
        """Return the exit code or None."""
        return self.exit_code


class FakeControlChannel:
    """``ControlChannel`` whose replies come from ``handler`` while ``running`` is true.

    ``hang_s`` > 0 makes every request fail with "did not answer" after spending ``hang_s``
    (or ``timeout_s`` if smaller) on ``clock``, like a silent service.
    """

    def __init__(
        self,
        handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        clock: FakeClock | None = None,
        hang_s: float = 0.0,
    ) -> None:
        """Start "not running"; ``handler`` answers requests once running."""
        self.running = False
        self.requests: list[dict[str, Any]] = []
        self.timeouts: list[float | None] = []
        self.clock = clock
        self.hang_s = hang_s
        self.handler = handler or (lambda req: {"ok": True, "status": {"service_running": True}})

    def request(self, message: dict[str, Any], timeout_s: float | None = None) -> dict[str, Any]:
        """Answer one request or raise ServiceNotRunning (or ServiceError when hung)."""
        from teams_avatar.errors import ServiceError, ServiceNotRunning

        if not self.running:
            raise ServiceNotRunning()
        self.requests.append(message)
        self.timeouts.append(timeout_s)
        if self.hang_s > 0 and self.clock is not None:
            self.clock.advance(min(self.hang_s, timeout_s if timeout_s is not None else 1e9))
            raise ServiceError("The camera service did not answer in time")
        return self.handler(message)


class FakeLauncher:
    """Service launcher that flips a fake channel to running when started.

    ``exit_code`` simulates a service that dies at once: the channel never starts and the
    returned handle reports that code.
    """

    def __init__(
        self,
        channel: FakeControlChannel,
        start_ok: bool = True,
        exit_code: int | None = None,
    ) -> None:
        """Bind to the channel it will "start"."""
        self.channel = channel
        self.start_ok = start_ok
        self.exit_code = exit_code
        self.starts = 0

    def start(self) -> FakeProcess:
        """Pretend to start the service process."""
        self.starts += 1
        if self.start_ok and self.exit_code is None:
            self.channel.running = True
        return FakeProcess(self.exit_code)


class FakeCameraSource:
    """``CameraSource`` producing counter frames; can refuse to open or fail mid-way."""

    def __init__(
        self,
        width: int = 320,
        height: int = 180,
        open_error: Exception | None = None,
        fail_after: int | None = None,
    ) -> None:
        """Create a closed fake camera."""
        from tests.clips import counter_frame

        self._frame = counter_frame
        self.width, self.height = width, height
        self.open_error = open_error
        self.fail_after = fail_after
        self.opened: list[str] = []
        self.reads = 0
        self.closed = False

    @property
    def size(self) -> tuple[int, int]:
        """Return the opened capture size."""
        return self.width, self.height

    def open(self, camera: CameraInfo) -> None:
        """Pretend to open ``camera``."""
        if self.open_error is not None:
            raise self.open_error
        self.opened.append(camera.name)

    def read(self) -> Frame:
        """Return the next counter frame."""
        if self.fail_after is not None and self.reads >= self.fail_after:
            from teams_avatar.errors import CameraUnavailableError

            raise CameraUnavailableError("Fake", "it was unplugged")
        frame = self._frame(self.reads, self.width, self.height)
        self.reads += 1
        return frame

    def close(self) -> None:
        """Mark the camera closed."""
        self.closed = True


class FakeRecordingUI:
    """``RecordingUI`` that presses ``key`` ("save", "cancel" or "closed") after N frames."""

    def __init__(self, key: str = "save", after: int = 60) -> None:
        """Script the key press."""
        self.key, self.after = key, after
        self.shown: list[float] = []
        self.closed_called = False

    def show(self, frame: Frame, elapsed_s: float) -> None:
        """Record the elapsed time shown with the frame."""
        self.shown.append(elapsed_s)

    def poll_key(self) -> str | None:
        """Return the scripted key once enough frames were shown."""
        if len(self.shown) >= self.after and self.key in ("save", "cancel"):
            return self.key
        return None

    def window_closed(self) -> bool:
        """Report the window closed once enough frames were shown, if scripted."""
        return self.key == "closed" and len(self.shown) >= self.after

    def close(self) -> None:
        """Mark the window closed."""
        self.closed_called = True


class FakePlaybackUI:
    """``PlaybackUI`` that records frames and presses Esc after ``esc_after`` frames."""

    def __init__(self, esc_after: int | None = None) -> None:
        """Script an optional Esc press."""
        self.esc_after = esc_after
        self.frames: list[Frame] = []
        self.closed_called = False

    def show(self, frame: Frame) -> None:
        """Keep the frame shown."""
        self.frames.append(frame)

    def poll_key(self) -> str | None:
        """Return "cancel" once ``esc_after`` frames were shown."""
        if self.esc_after is not None and len(self.frames) >= self.esc_after:
            return "cancel"
        return None

    def window_closed(self) -> bool:
        """The fake window is never closed by the user."""
        return False

    def close(self) -> None:
        """Mark the window closed."""
        self.closed_called = True
