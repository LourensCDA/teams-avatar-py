"""PyAV file adapter: H.264/MP4 writing, frame-exact reading and probing (research R3).

This is the only module that uses PyAV for files. Every PyAV/OS failure is translated into
``UnreadableVideoError`` so callers never see library exceptions.
"""

from __future__ import annotations

import bisect
import itertools
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol, Self, runtime_checkable

import av
import av.error
import cv2
import numpy as np
import numpy.typing as npt

from teams_avatar.errors import UnreadableVideoError

Frame = npt.NDArray[np.uint8]

# One keyframe per second at 30 fps: a loop-repeat seek decodes at most 29 frames.
KEYFRAME_INTERVAL = 30
_CRF = "20"
_PRESET = "veryfast"
_AV_ERRORS = (av.error.FFmpegError, OSError, ValueError, EOFError)


@dataclass(frozen=True)
class VideoInfo:
    """Basic facts about a video file."""

    duration_s: float
    frame_count: int
    width: int
    height: int
    fps: float


@runtime_checkable
class VideoWriter(Protocol):
    """Sink for BGR frames; other modules and test fakes depend on this, not on PyAV."""

    def write(self, frame: Frame) -> None:
        """Append one BGR uint8 ``(height, width, 3)`` frame."""
        ...

    def close(self) -> None:
        """Finish the file; safe to call more than once."""
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


@runtime_checkable
class VideoReader(Protocol):
    """Frame-exact source of BGR frames."""

    width: int
    height: int
    fps: float
    frame_count: int

    def read(self) -> Frame | None:
        """Return the next frame, or ``None`` after the last one."""
        ...

    def frame_time(self, index: int) -> float:
        """Return the presentation time of frame ``index`` in seconds from the first frame."""
        ...

    def seek(self, index: int) -> None:
        """Make the next ``read`` return exactly frame ``index``."""
        ...

    def close(self) -> None:
        """Release the file; safe to call more than once."""
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


def _to_bgr(frame: av.VideoFrame) -> Frame:
    """Convert a decoded frame to BGR.

    swscale's bgr24 conversion spins several threads (about 0.2 of a core at 720p30), so the
    common case, an even-sized yuv420p frame, is converted with OpenCV on the calling thread.
    Other formats and odd sizes (which yuv420p cannot represent exactly) use PyAV.
    """
    if frame.format.name == "yuv420p" and frame.width % 2 == 0 and frame.height % 2 == 0:
        planar = frame.to_ndarray(format="yuv420p")
        return np.asarray(cv2.cvtColor(planar, cv2.COLOR_YUV2BGR_I420), dtype=np.uint8)
    return np.asarray(frame.to_ndarray(format="bgr24"), dtype=np.uint8)


class PyAVWriter:
    """Write BGR frames as H.264 (libx264 veryfast, CRF 20, yuv420p) in an MP4 container."""

    def __init__(self, path: Path, width: int, height: int, fps: int = 30) -> None:
        """Open ``path`` for writing exactly as given (callers handle ``.partial`` renames).

        Raises:
            ValueError: If the size is not positive and even (yuv420p needs even sides).
            UnreadableVideoError: If the file cannot be created.
        """
        if width <= 0 or height <= 0 or width % 2 or height % 2:
            raise ValueError(f"width and height must be positive and even, got {width}x{height}")
        self._shape = (height, width, 3)
        self._container: Any = None
        self._closed = False
        try:
            self._container = av.open(str(path), mode="w")
            stream = self._container.add_stream("libx264", rate=fps)
            stream.width = width
            stream.height = height
            stream.pix_fmt = "yuv420p"
            # sc_threshold=0 disables scene-cut keyframes so keyframes fall exactly every 30
            # frames, which the loop-repeat seek relies on.
            stream.options = {
                "crf": _CRF,
                "preset": _PRESET,
                "g": str(KEYFRAME_INTERVAL),
                "sc_threshold": "0",
            }
            self._stream = stream
        except _AV_ERRORS as exc:
            self._release()
            raise UnreadableVideoError(f"the file cannot be created ({_reason(exc)})") from exc

    def write(self, frame: Frame) -> None:
        """Encode one frame.

        Raises:
            ValueError: If the frame is not a uint8 array of the writer's ``(h, w, 3)`` shape.
        """
        if self._closed:
            raise ValueError("writer is closed")
        if frame.dtype != np.uint8 or frame.shape != self._shape:
            raise ValueError(
                f"expected uint8 frame of shape {self._shape}, got {frame.dtype} {frame.shape}"
            )
        video_frame = av.VideoFrame.from_ndarray(np.ascontiguousarray(frame), format="bgr24")
        try:
            for packet in self._stream.encode(video_frame):
                self._container.mux(packet)
        except _AV_ERRORS as exc:
            raise UnreadableVideoError(f"encoding failed ({_reason(exc)})") from exc

    def close(self) -> None:
        """Flush the encoder and finalise the MP4."""
        if self._closed:
            return
        self._closed = True
        try:
            for packet in self._stream.encode():
                self._container.mux(packet)
        except _AV_ERRORS as exc:
            raise UnreadableVideoError(f"encoding failed ({_reason(exc)})") from exc
        finally:
            self._release()

    def _release(self) -> None:
        """Close the container without flushing."""
        if self._container is not None:
            try:
                self._container.close()
            except _AV_ERRORS:
                pass
            self._container = None

    def __enter__(self) -> Self:
        """Return the writer."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Finalise on success; on error just release the file so the original error shows."""
        if exc_type is None:
            self.close()
        else:
            self._closed = True
            self._release()


class PyAVReader:
    """Frame-exact reader that seeks via keyframes instead of decoding from the start."""

    def __init__(self, path: Path) -> None:
        """Open ``path``, index its packets and prepare decoding.

        Raises:
            UnreadableVideoError: If the file is missing, not a video, truncated or has no
                video stream.
        """
        self._container: Any = None
        self._frames: Iterator[Any] | None = None
        self._next = 0
        self._rotation = 0
        try:
            self._container = av.open(str(path), mode="r")
            if not self._container.streams.video:
                raise UnreadableVideoError("the file has no video track")
            stream = self._container.streams.video[0]
            # One decoder thread: libav's worker threads spin-wait, and playback needs one frame
            # per 33 ms, not maximum decode speed.
            stream.codec_context.thread_count = 1
            self._stream = stream
            self.width: int = int(stream.codec_context.width)
            self.height: int = int(stream.codec_context.height)
            rate = stream.average_rate or stream.guessed_rate
            if not rate or rate <= 0:
                raise UnreadableVideoError("the video has no usable frame rate")
            self.fps: float = float(rate)
            self._time_base = float(stream.time_base) if stream.time_base else 0.0
            self._index_packets()
            if self.frame_count == 0 or self.width <= 0 or self.height <= 0:
                raise UnreadableVideoError("the video contains no frames")
            self._start_decoding(0)
            if self._rotation in (90, 270):
                # Report the displayed size so callers never see a sideways picture.
                self.width, self.height = self.height, self.width
        except UnreadableVideoError:
            self.close()
            raise
        except _AV_ERRORS as exc:
            self.close()
            raise UnreadableVideoError(_reason(exc)) from exc

    def _index_packets(self) -> None:
        """Demux (no decoding) to count frames exactly and map keyframes to frame numbers.

        Container-reported counts can be wrong or absent, and with B-frames the keyframe's
        presentation rank differs from its packet order, so rank packets by pts.
        """
        pts_list: list[int] = []
        key_pts: list[int] = []
        for packet in self._container.demux(self._stream):
            if packet.pts is None or packet.is_discard:
                continue
            pts_list.append(packet.pts)
            if packet.is_keyframe:
                key_pts.append(packet.pts)
        pts_list.sort()
        self.frame_count: int = len(pts_list)
        self._pts = pts_list
        self._key_pts = sorted(key_pts)
        self._key_index = [bisect.bisect_left(pts_list, p) for p in self._key_pts]

    def _start_decoding(self, index: int) -> None:
        """Restart decoding at the keyframe at or before ``index``."""
        if not self._key_pts:
            raise UnreadableVideoError("the video has no keyframes")
        slot = max(bisect.bisect_right(self._key_index, index) - 1, 0)
        self._container.seek(self._key_pts[slot], stream=self._stream, backward=True)
        frames = self._container.decode(self._stream)
        # The demuxer may land on an earlier keyframe than asked; locate by the frame's pts.
        first = next(frames, None)
        if first is None:
            raise UnreadableVideoError("the video ended before the requested frame")
        self._next = bisect.bisect_left(self._pts, first.pts)
        # Phone videos store landscape pixels plus a display-matrix rotation (counter-clockwise
        # degrees); it is only exposed on decoded frames, so read it from the first one.
        self._rotation = (int(getattr(first, "rotation", 0) or 0) // 90 * 90) % 360
        self._frames = itertools.chain([first], frames)

    def read(self) -> Frame | None:
        """Return the next BGR frame, or ``None`` after the last one."""
        if self._frames is None or self._next >= self.frame_count:
            return None
        try:
            frame = next(self._frames, None)
            if frame is None:
                return None
            self._next += 1
            image = _to_bgr(frame)
            if self._rotation:
                image = np.ascontiguousarray(np.rot90(image, self._rotation // 90))
        except _AV_ERRORS as exc:
            raise UnreadableVideoError(f"a frame could not be decoded ({_reason(exc)})") from exc
        return image

    def frame_time(self, index: int) -> float:
        """Return the presentation time of frame ``index`` in seconds from the first frame.

        Raises:
            ValueError: If ``index`` is outside ``0..frame_count-1``.
        """
        if not 0 <= index < self.frame_count:
            raise ValueError(f"frame {index} is outside 0..{self.frame_count - 1}")
        if self._time_base <= 0:
            return index / self.fps
        return (self._pts[index] - self._pts[0]) * self._time_base

    def seek(self, index: int) -> None:
        """Position so the next ``read`` returns frame ``index``.

        Raises:
            ValueError: If ``index`` is outside ``0..frame_count-1``.
            UnreadableVideoError: If decoding fails on the way to the frame.
        """
        if not 0 <= index < self.frame_count:
            raise ValueError(f"frame {index} is outside 0..{self.frame_count - 1}")
        try:
            slot = max(bisect.bisect_right(self._key_index, index) - 1, 0)
            # Already in the target's GOP and not past it: decoding forward is cheaper than
            # a container seek.
            in_gop = self._frames is not None and self._key_index[slot] <= self._next <= index
            if not in_gop:
                self._start_decoding(index)
            while self._next < index:
                if self._frames is None or next(self._frames, None) is None:
                    raise UnreadableVideoError("the video ended before the requested frame")
                self._next += 1
        except _AV_ERRORS as exc:
            raise UnreadableVideoError(f"seeking failed ({_reason(exc)})") from exc

    def close(self) -> None:
        """Release the file."""
        self._frames = None
        if self._container is not None:
            try:
                self._container.close()
            except _AV_ERRORS:
                pass
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
    """Return size, exact frame count, frame rate and duration of a video file.

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


def _reason(exc: BaseException) -> str:
    """Turn a library exception into a short plain-language reason."""
    if isinstance(exc, FileNotFoundError):
        return "the file does not exist"
    if isinstance(exc, PermissionError):
        return "the file cannot be opened (permission denied)"
    if isinstance(exc, av.error.InvalidDataError):
        return "the file is not a valid or complete video"
    return f"the file is not a readable video ({exc})"
