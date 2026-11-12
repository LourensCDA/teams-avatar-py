"""PyAV file codec adapter: H.264/MP4 writing, seekable reading and probing (research R3).

This is the only module that touches PyAV for video files. Every failure to open, probe or
decode is reported as ``UnreadableVideoError`` so callers never see raw ``av`` exceptions.
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
CRF = "20"
PRESET = "veryfast"


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
        """Append one BGR uint8 ``(height, width, 3)`` frame."""

    def close(self) -> None:
        """Flush and finish the file; safe to call twice."""

    def __enter__(self) -> Self:
        """Enter the context."""

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the writer."""


class VideoReader(Protocol):
    """Reads BGR frames sequentially with random-access seek."""

    width: int
    height: int
    fps: float
    frame_count: int

    def read(self) -> Frame | None:
        """Return the next BGR frame, or None at the end."""

    def seek(self, index: int) -> None:
        """Make the next ``read`` return exactly frame ``index``."""

    def close(self) -> None:
        """Release the file; safe to call twice."""

    def __enter__(self) -> Self:
        """Enter the context."""

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the reader."""


class PyAVWriter:
    """Encodes frames as H.264 (libx264, veryfast, CRF 20, yuv420p) into ``path``."""

    def __init__(self, path: Path, width: int, height: int, fps: int = 30) -> None:
        """Open ``path`` for writing.

        Args:
            path: Output file; written as given (callers handle ``.partial`` renames).
            width: Frame width, must be even for yuv420p.
            height: Frame height, must be even for yuv420p.
            fps: Frame rate.

        Raises:
            ValueError: If the size is not a positive even number.
        """
        if width <= 0 or height <= 0 or width % 2 or height % 2:
            raise ValueError(f"width and height must be positive and even, got {width}x{height}")
        self._width = width
        self._height = height
        self._closed = False
        self._container = av.open(str(path), mode="w", format="mp4")
        try:
            self._stream: Any = self._container.add_stream("libx264", rate=fps)
            self._stream.width = width
            self._stream.height = height
            self._stream.pix_fmt = "yuv420p"
            # Fixed GOP (and no scene-cut keyframes) so the loop-repeat seek stays cheap.
            self._stream.options = {
                "crf": CRF,
                "preset": PRESET,
                "g": str(KEYFRAME_INTERVAL),
                "keyint_min": str(KEYFRAME_INTERVAL),
                "sc_threshold": "0",
            }
        except BaseException:
            self._container.close()
            raise

    def write(self, frame: Frame) -> None:
        """Encode one BGR uint8 frame.

        Raises:
            ValueError: If the frame is not uint8 with shape ``(height, width, 3)``.
        """
        if frame.dtype != np.uint8 or frame.shape != (self._height, self._width, 3):
            raise ValueError(
                f"expected uint8 frame of shape {(self._height, self._width, 3)}, "
                f"got {frame.dtype} {frame.shape}"
            )
        video_frame = av.VideoFrame.from_ndarray(frame, format="bgr24")
        for packet in self._stream.encode(video_frame):
            self._container.mux(packet)

    def close(self) -> None:
        """Flush the encoder and close the file."""
        if self._closed:
            return
        self._closed = True
        try:
            for packet in self._stream.encode():
                self._container.mux(packet)
        finally:
            self._container.close()

    def __enter__(self) -> Self:
        """Enter the context."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the writer."""
        self.close()


def _open_video(path: Path) -> tuple[Any, Any]:
    """Open ``path`` and return ``(container, video_stream)``.

    Raises:
        UnreadableVideoError: If the file is missing, not a video or has no video stream.
    """
    try:
        container = av.open(str(path), mode="r")
    except FileNotFoundError:
        raise UnreadableVideoError("the file does not exist") from None
    except (av.error.FFmpegError, OSError, ValueError) as exc:
        raise UnreadableVideoError("the file is not a video or is damaged") from exc
    if not container.streams.video:
        container.close()
        raise UnreadableVideoError("the file has no video track")
    return container, container.streams.video[0]


def _scan_packets(
    container: av.container.InputContainer, stream: av.video.stream.VideoStream
) -> tuple[int, list[tuple[int, int]]]:
    """Demux the whole file once to get exact presentation order.

    Returns:
        ``(frame_count, keyframes)`` where ``keyframes`` is a sorted list of
        ``(frame_index, pts)`` for keyframe packets, indexed in presentation order.
    """
    pts_list: list[int] = []
    key_pts: list[int] = []
    for packet in container.demux(stream):
        if packet.size == 0:  # flush packet at the end of the stream
            continue
        stamp = packet.pts if packet.pts is not None else packet.dts
        if stamp is None:
            raise UnreadableVideoError("the video has no usable timestamps")
        pts_list.append(stamp)
        if packet.is_keyframe:
            key_pts.append(stamp)
    pts_list.sort()
    rank = {stamp: i for i, stamp in enumerate(pts_list)}
    keyframes = sorted((rank[stamp], stamp) for stamp in key_pts)
    return len(pts_list), keyframes


class PyAVReader:
    """Sequential BGR frame reader with keyframe-based seek; audio is ignored."""

    def __init__(self, path: Path) -> None:
        """Open ``path`` and index its frames.

        Raises:
            UnreadableVideoError: If the file cannot be opened or indexed.
        """
        self._container: Any = None
        container, stream = _open_video(path)
        self._container = container
        self._stream = stream
        try:
            stream.thread_type = "AUTO"
            self.width: int = stream.codec_context.width
            self.height: int = stream.codec_context.height
            rate = stream.average_rate or stream.guessed_rate
            if not rate or self.width <= 0 or self.height <= 0:
                raise UnreadableVideoError("the video has no valid size or frame rate")
            self.fps: float = float(rate)
            # Counting by demuxing (cheap, no decode) keeps frame_count exact for every
            # container, including ones whose header count is missing or wrong.
            self.frame_count: int
            self.frame_count, self._keyframes = _scan_packets(container, stream)
            if self.frame_count == 0 or not self._keyframes:
                raise UnreadableVideoError("the video contains no frames")
            seconds = container.duration / av.time_base if container.duration else None
            self.duration_s: float = seconds or self.frame_count / self.fps
            self._position = 0
            self._rewind()
        except UnreadableVideoError:
            self.close()
            raise
        except (av.error.FFmpegError, OSError, ValueError) as exc:
            self.close()
            raise UnreadableVideoError("the file is damaged or incomplete") from exc

    def _rewind(self) -> None:
        """Restart decoding at the first frame."""
        self._container.seek(0, backward=True, any_frame=False)
        self._restart_decoder()
        self._position = 0

    def _restart_decoder(self) -> None:
        """Drop buffered decoder state and start a fresh frame iterator."""
        self._stream.codec_context.flush_buffers()
        self._frames: Iterator[av.VideoFrame] = self._container.decode(self._stream)

    def _next_frame(self) -> av.VideoFrame | None:
        """Return the next decoded frame or None at the end."""
        try:
            return next(self._frames, None)
        except (av.error.FFmpegError, OSError, ValueError) as exc:
            raise UnreadableVideoError("a frame could not be decoded") from exc

    def read(self) -> Frame | None:
        """Return the next BGR frame, or None after the last one."""
        frame = self._next_frame()
        if frame is None:
            return None
        self._position += 1
        try:
            # bgr24 always yields uint8; PyAV stubs only declare the union of all dtypes.
            array = cast(Frame, frame.to_ndarray(format="bgr24"))
        except (av.error.FFmpegError, ValueError) as exc:
            raise UnreadableVideoError("a frame could not be converted") from exc
        return array

    def seek(self, index: int) -> None:
        """Position so the next ``read`` returns frame ``index`` exactly.

        Jumps to the nearest keyframe at or before ``index`` and decodes forward, so the cost
        is bounded by the keyframe interval rather than the clip length.

        Raises:
            ValueError: If ``index`` is outside ``[0, frame_count)``.
            UnreadableVideoError: If decoding fails.
        """
        if not 0 <= index < self.frame_count:
            raise ValueError(f"frame index {index} out of range 0..{self.frame_count - 1}")
        if index == self._position:
            return
        key_index, key_pts = max(k for k in self._keyframes if k[0] <= index)
        try:
            self._container.seek(key_pts, stream=self._stream, backward=True, any_frame=False)
        except (av.error.FFmpegError, OSError, ValueError) as exc:
            raise UnreadableVideoError("the video could not be repositioned") from exc
        self._restart_decoder()
        position = key_index
        while position < index:
            if self._next_frame() is None:
                raise UnreadableVideoError("the video ended unexpectedly while seeking")
            position += 1
        self._position = index

    def close(self) -> None:
        """Release the file; safe to call twice."""
        container, self._container = self._container, None
        if container is not None:
            container.close()

    def __enter__(self) -> Self:
        """Enter the context."""
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
    """Return size, frame count, frame rate and duration of a video file.

    Raises:
        UnreadableVideoError: If the file cannot be opened or read.
    """
    with PyAVReader(path) as reader:
        seconds = reader.duration_s
        return VideoInfo(
            duration_s=seconds,
            frame_count=reader.frame_count,
            width=reader.width,
            height=reader.height,
            fps=reader.fps,
        )
