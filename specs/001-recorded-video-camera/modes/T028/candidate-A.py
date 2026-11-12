"""PyAV adapter: H.264/MP4 file writing, seekable reading and probing.

This is the only module that touches PyAV for files. Every PyAV or OS failure is converted to
``UnreadableVideoError`` so callers never see raw ``av`` exceptions.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol, Self, cast

import av
import av.error
import numpy as np
import numpy.typing as npt

from teams_avatar.errors import UnreadableVideoError

Frame = npt.NDArray[np.uint8]

KEYFRAME_INTERVAL = 30
_CRF = "20"
_PRESET = "veryfast"


@dataclass(frozen=True)
class VideoInfo:
    """Basic facts about a video file."""

    duration_s: float
    frame_count: int
    width: int
    height: int
    fps: float


class VideoWriter(Protocol):
    """Writes BGR frames to a video file."""

    def write(self, frame: Frame) -> None:
        """Append one ``(height, width, 3)`` uint8 BGR frame."""
        ...

    def close(self) -> None:
        """Flush the encoder and finalise the file."""
        ...

    def __enter__(self) -> Self:
        """Return the writer."""
        ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the writer."""
        ...


class VideoReader(Protocol):
    """Reads BGR frames from a video file with exact seeking."""

    width: int
    height: int
    fps: float
    frame_count: int

    def read(self) -> Frame | None:
        """Return the next frame, or None at the end."""
        ...

    def seek(self, index: int) -> None:
        """Position so the next ``read()`` returns frame ``index``."""
        ...

    def close(self) -> None:
        """Release the file."""
        ...

    def __enter__(self) -> Self:
        """Return the reader."""
        ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the reader."""
        ...


class PyAVWriter:
    """Encodes frames as H.264 (libx264, CRF 20, yuv420p) into an MP4 at the given path."""

    def __init__(self, path: Path, width: int, height: int, fps: int = 30) -> None:
        """Open ``path`` for writing.

        Args:
            path: Output file; written as given (callers handle ``.partial.mp4`` renames).
            width: Frame width in pixels (even, as yuv420p requires).
            height: Frame height in pixels (even).
            fps: Frame rate.

        Raises:
            ValueError: If the size is not positive and even.
        """
        if width <= 0 or height <= 0 or width % 2 or height % 2:
            raise ValueError(f"width and height must be positive and even, got {width}x{height}")
        self._width = width
        self._height = height
        self._container = av.open(str(path), mode="w")
        try:
            stream = self._container.add_stream("libx264", rate=fps)
            stream.width = width
            stream.height = height
            stream.pix_fmt = "yuv420p"
            # scenecut=0 keeps keyframes exactly every 30 frames so loop-repeat seeks stay cheap.
            stream.options = {
                "crf": _CRF,
                "preset": _PRESET,
                "g": str(KEYFRAME_INTERVAL),
                "x264-params": f"keyint={KEYFRAME_INTERVAL}:min-keyint={KEYFRAME_INTERVAL}"
                ":scenecut=0",
            }
        except BaseException:
            self._container.close()
            raise
        self._stream = stream
        self._closed = False

    def write(self, frame: Frame) -> None:
        """Encode one BGR frame.

        Raises:
            ValueError: If the frame is not a ``(height, width, 3)`` uint8 array.
        """
        if (
            not isinstance(frame, np.ndarray)
            or frame.dtype != np.uint8
            or frame.shape != (self._height, self._width, 3)
        ):
            raise ValueError(
                f"frame must be uint8 with shape ({self._height}, {self._width}, 3), "
                f"got {getattr(frame, 'dtype', None)} {getattr(frame, 'shape', None)}"
            )
        video_frame = av.VideoFrame.from_ndarray(frame, format="bgr24")
        for packet in self._stream.encode(video_frame):
            self._container.mux(packet)

    def close(self) -> None:
        """Flush the encoder and close the file; safe to call twice."""
        if self._closed:
            return
        self._closed = True
        try:
            for packet in self._stream.encode():
                self._container.mux(packet)
        finally:
            self._container.close()

    def __enter__(self) -> Self:
        """Return the writer."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the writer."""
        self.close()


def _unreadable(path: Path, detail: str) -> UnreadableVideoError:
    """Build the error for a file that cannot be opened or decoded."""
    return UnreadableVideoError(f"{path.name}: {detail}")


def _chain(first: av.VideoFrame, rest: Iterator[av.VideoFrame]) -> Iterator[av.VideoFrame]:
    """Yield ``first`` then everything in ``rest``."""
    yield first
    yield from rest


class PyAVReader:
    """Sequential reader with keyframe-based exact seeking; ignores audio."""

    def __init__(self, path: Path) -> None:
        """Open ``path`` and read its stream properties.

        Raises:
            UnreadableVideoError: If the file is missing, not a video, truncated or has no
                video stream.
        """
        self._path = Path(path)
        self._container: Any = None
        try:
            self._container = av.open(str(self._path), mode="r")
            if not self._container.streams.video:
                raise _unreadable(self._path, "it has no video stream")
            self._stream: Any = self._container.streams.video[0]
            self._stream.thread_type = "AUTO"
            self.width: int = int(self._stream.codec_context.width)
            self.height: int = int(self._stream.codec_context.height)
            rate = self._stream.average_rate or self._stream.guessed_rate
            if not rate or self.width <= 0 or self.height <= 0:
                raise _unreadable(self._path, "its video size or frame rate is unknown")
            self.fps: float = float(rate)
            self._start: int = int(self._stream.start_time or 0)
            self.frame_count: int = self._count_frames()
            if self.frame_count <= 0:
                raise _unreadable(self._path, "it contains no video frames")
            self._decoder: Iterator[av.VideoFrame] = iter(())
            self._rewind()
        except UnreadableVideoError:
            self.close()
            raise
        except (av.error.FFmpegError, OSError, ValueError) as exc:
            self.close()
            raise _unreadable(self._path, f"it could not be opened ({exc})") from exc

    def _count_frames(self) -> int:
        """Return the exact frame count, demuxing when the container does not report it."""
        if self._stream.frames > 0:
            return int(self._stream.frames)
        count = sum(1 for p in self._container.demux(self._stream) if p.size > 0)
        self._container.seek(0)
        return count

    def _rewind(self) -> None:
        """Restart decoding from the first frame."""
        self._container.seek(self._start, stream=self._stream, backward=True)
        self._decoder = iter(self._container.decode(self._stream))

    def _frame_index(self, frame: av.VideoFrame) -> int:
        """Map a decoded frame's timestamp to its zero-based frame number."""
        pts = frame.pts if frame.pts is not None else self._start
        seconds = float((pts - self._start) * self._stream.time_base)
        return round(seconds * self.fps)

    def read(self) -> Frame | None:
        """Return the next BGR frame, or None at the end.

        Raises:
            UnreadableVideoError: If decoding fails.
        """
        if self._container is None:
            raise ValueError("reader is closed")
        try:
            frame = next(self._decoder, None)
            if frame is None:
                return None
            # bgr24 is always uint8; av's stubs only know the union of possible dtypes.
            return cast(Frame, frame.to_ndarray(format="bgr24"))
        except (av.error.FFmpegError, OSError) as exc:
            raise _unreadable(self._path, f"it could not be decoded ({exc})") from exc

    def seek(self, index: int) -> None:
        """Position so the next ``read()`` returns frame ``index``.

        Jumps to the nearest keyframe at or before the target and decodes forward, rather than
        decoding from the start.

        Raises:
            ValueError: If ``index`` is outside ``0..frame_count-1``.
            UnreadableVideoError: If seeking or decoding fails.
        """
        if self._container is None:
            raise ValueError("reader is closed")
        if not 0 <= index < self.frame_count:
            raise ValueError(f"frame {index} is outside 0..{self.frame_count - 1}")
        try:
            if index == 0:
                self._rewind()
                return
            target_pts = self._start + round(index / self.fps / self._stream.time_base)
            self._container.seek(target_pts, stream=self._stream, backward=True)
            decoder = iter(self._container.decode(self._stream))
            for frame in decoder:
                if self._frame_index(frame) >= index:
                    # Re-feed the frame we peeked at so read() returns it.
                    self._decoder = _chain(frame, decoder)
                    return
            raise _unreadable(self._path, f"frame {index} could not be reached")
        except (av.error.FFmpegError, OSError) as exc:
            raise _unreadable(self._path, f"it could not be seeked ({exc})") from exc

    def close(self) -> None:
        """Release the file; safe to call twice."""
        if self._container is not None:
            self._container.close()
            self._container = None

    def __enter__(self) -> Self:
        """Return the reader."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the reader."""
        self.close()


def probe(path: Path) -> VideoInfo:
    """Read a video file's size, rate, exact frame count and duration.

    Raises:
        UnreadableVideoError: If the file cannot be opened as a video.
    """
    with PyAVReader(path) as reader:
        return VideoInfo(
            duration_s=reader.frame_count / reader.fps,
            frame_count=reader.frame_count,
            width=reader.width,
            height=reader.height,
            fps=reader.fps,
        )
