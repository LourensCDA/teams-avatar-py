"""Endless looping of a recording with a crossfade at the repeat point (FR-007, research R5).

A hard jump from the last frame to the first is what OBS's own virtual camera does and what makes
a loop obvious. Here the last ``BLEND_FRAMES`` frames fade into the first ``BLEND_FRAMES`` frames,
and every repeat then resumes *after* those start frames, so the picture never jumps.
"""

from __future__ import annotations

from collections.abc import Iterator

import cv2
import numpy as np
from numpy.typing import NDArray

from teams_avatar.adapters.pyav_codec import VideoReader
from teams_avatar.errors import UnreadableVideoError
from teams_avatar.frames import fit_on_canvas

Frame = NDArray[np.uint8]

BLEND_FRAMES = 30
# Below this length the fade would eat too much of the clip, so it repeats plainly.
MIN_BLEND_FRAMES = 150


class LoopPlayer:
    """Yield an endless, crossfaded loop of a recording as canvas-sized frames.

    Only the first ``BLEND_FRAMES`` fitted frames are kept in memory. Not thread-safe: use it from
    one decoder thread. Reader errors (``UnreadableVideoError``) propagate to the caller.
    """

    def __init__(self, reader: VideoReader, canvas_w: int, canvas_h: int) -> None:
        """Wrap ``reader``; frames come out fitted on a ``canvas_w`` x ``canvas_h`` canvas."""
        self._reader = reader
        self._w = canvas_w
        self._h = canvas_h
        self._count = reader.frame_count
        self._blend = self._count >= MIN_BLEND_FRAMES
        self._cache: list[Frame] = []
        self._used = False
        self._frames = self._endless()

    @property
    def loop_length(self) -> int:
        """Number of distinct frames in one visible repeat (the fade overlaps ``BLEND_FRAMES``)."""
        return self._count - BLEND_FRAMES if self._blend else self._count

    @property
    def cached_frames(self) -> int:
        """How many start frames are held in memory."""
        return len(self._cache)

    def next_frame(self) -> Frame:
        """Return the next frame of the infinite loop; callers must not modify it in place."""
        return next(self._frames)

    def one_pass(self) -> Iterator[Frame]:
        """Yield one full pass including the end blend, for previews."""
        return self._first_pass()

    def _endless(self) -> Iterator[Frame]:
        """Play the first pass, then repeat the loop section forever."""
        yield from self._first_pass()
        while True:
            produced = False
            for frame in self._repeat():
                produced = True
                yield frame
            if not produced:
                # A reader that yields nothing after a seek would otherwise spin forever.
                raise UnreadableVideoError("the video returned no frames when repeating")

    def _mix(self, end: Frame, start: Frame, weight: float) -> Frame:
        """Return ``end * (1 - weight) + start * weight`` in a new array."""
        return np.asarray(cv2.addWeighted(end, 1.0 - weight, start, weight, 0.0), dtype=np.uint8)

    def _first_pass(self) -> Iterator[Frame]:
        """Decode from frame 0, caching the start frames and blending the tail."""
        if self._used:
            self._reader.seek(0)
        self._used = True
        tail = self._count - BLEND_FRAMES
        index = 0
        while index < self._count:
            raw = self._reader.read()
            if raw is None:
                break  # the file holds fewer frames than it claims; this is the real end
            frame = fit_on_canvas(raw, self._w, self._h)
            if self._blend and index < BLEND_FRAMES and index >= len(self._cache):
                # Copy: fit_on_canvas may hand back the reader's own buffer.
                self._cache.append(frame.copy())
            if self._blend and index >= tail:
                k = index - tail
                frame = self._mix(frame, self._cache[k], (k + 1) / (BLEND_FRAMES + 1))
            yield frame
            index += 1
        if index == 0:
            raise UnreadableVideoError("the video has no frames")
        if index < self._count:
            self._shrink_to(index)

    def _shrink_to(self, count: int) -> None:
        """Adopt the real frame count found when the reader ended early."""
        self._count = count
        if count < MIN_BLEND_FRAMES or len(self._cache) < BLEND_FRAMES:
            self._blend = False
            self._cache.clear()

    def _repeat(self) -> Iterator[Frame]:
        """Play one repeat: skip the cached start, then the middle, then the blended tail."""
        first = BLEND_FRAMES if self._blend else 0
        self._reader.seek(first)
        index = first
        while index < self._count:
            raw = self._reader.read()
            if raw is None:
                self._shrink_to(index)
                return
            frame = fit_on_canvas(raw, self._w, self._h)
            tail = self._count - BLEND_FRAMES
            if self._blend and index >= tail:
                k = index - tail
                frame = self._mix(frame, self._cache[k], (k + 1) / (BLEND_FRAMES + 1))
            yield frame
            index += 1
