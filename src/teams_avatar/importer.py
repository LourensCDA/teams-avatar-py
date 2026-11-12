"""Import an existing video into the library (FR-021, FR-022; research R3, R6, R8).

The source is only ever read. It is decoded frame by frame, resampled to 30 fps by presentation
time, capped in size and re-encoded video-only into ``recordings/<id>.mp4``.
"""

from __future__ import annotations

import bisect
import errno
import logging
import os
import shutil
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np

from teams_avatar import analysis, frames, logging_setup
from teams_avatar.activity import ActivityLog
from teams_avatar.adapters.pyav_codec import Frame, PyAVReader, PyAVWriter, probe
from teams_avatar.errors import (
    ImportRejectedError,
    LibraryWriteError,
    StorageFullError,
    TeamsAvatarError,
    UnreadableVideoError,
)
from teams_avatar.library import Library, default_name
from teams_avatar.models import ActivityRecord, Recording, RecordResult
from teams_avatar.paths import DataPaths

logger = logging.getLogger(__name__)

OUTPUT_FPS = 30
MIN_FREE_BYTES = 200 * 1024 * 1024
# Absorbs float error so a frame due exactly at an output tick is not delayed by one tick.
_TIME_EPSILON = 1e-4


class _SourceFailure(Exception):
    """The source video is unreadable or damaged (internal; becomes ``ImportRejectedError``)."""

    def __init__(self, reason: str) -> None:
        """Keep the plain-language reason."""
        super().__init__(reason)
        self.reason = reason


class _DestinationFailure(Exception):
    """The recording could not be written (internal; becomes a storage error)."""

    def __init__(self, cause: BaseException) -> None:
        """Keep the underlying exception for errno inspection."""
        super().__init__(str(cause))
        self.cause = cause


def _is_disk_full(exc: BaseException | None) -> bool:
    """Return True if ``exc`` or anything it was raised from reports ENOSPC."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if getattr(exc, "errno", None) == errno.ENOSPC:
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def _record_rejection(activity: ActivityLog, reason: str, session_id: str) -> None:
    """Append an ``import_rejected`` activity record carrying ``reason``."""
    logger.info("import rejected: %s", reason)
    try:
        activity.append(
            ActivityRecord(
                at=datetime.now(UTC),
                event="import_rejected",
                recording_id=None,
                recording_name=None,
                session_id=session_id,
                outcome="rejected",
                detail=reason,
            )
        )
    except Exception:
        # The real rejection must reach the user even if the log cannot be written.
        logger.warning("could not write the activity record", exc_info=True)


def _fit(frame: Frame, width: int, height: int) -> Frame:
    """Return ``frame`` at exactly ``width`` x ``height``, shrinking or stretching if needed.

    Checked per frame because a stream can change size mid-file, and the writer refuses any
    frame that does not match its header.
    """
    if frame.shape[1] == width and frame.shape[0] == height:
        return frame
    # INTER_AREA averages pixels instead of dropping them, which is right for shrinking.
    return np.asarray(cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA), np.uint8)


def _read_frame(reader: PyAVReader) -> Frame:
    """Return the next source frame or raise ``_SourceFailure`` if it cannot be decoded."""
    try:
        frame = reader.read()
    except UnreadableVideoError as exc:
        raise _SourceFailure(exc.reason) from exc
    if frame is None:
        raise _SourceFailure("the video ends before its last frame")
    return frame


def _transcode(source: Path, partial: Path) -> None:
    """Decode ``source`` and write it to ``partial`` at 30 fps, capped in size, video only.

    Output frame ``k`` shows the latest source frame at or before time ``k / 30``, using each
    frame's real presentation time so variable-frame-rate files keep their duration.

    Raises:
        _SourceFailure: If the source cannot be fully decoded.
        _DestinationFailure: If the output cannot be written.
    """
    try:
        reader = PyAVReader(source)
    except UnreadableVideoError as exc:
        raise _SourceFailure(exc.reason) from exc
    with reader:
        out_w, out_h = frames.cap_size(reader.width, reader.height)
        count = reader.frame_count
        times = [reader.frame_time(i) for i in range(count)]
        # The last frame has no successor to give its duration; assume the previous gap.
        last_gap = times[-1] - times[-2] if count > 1 else 1 / reader.fps
        total = max(1, round((times[-1] + last_gap) * OUTPUT_FPS))
        current_index = -1
        prepared: Frame | None = None
        try:
            with PyAVWriter(partial, out_w, out_h, OUTPUT_FPS) as writer:
                for k in range(total):
                    due = k / OUTPUT_FPS + _TIME_EPSILON
                    wanted = max(0, bisect.bisect_right(times, due) - 1)
                    while current_index < wanted:
                        decoded = _read_frame(reader)
                        current_index += 1
                        prepared = _fit(decoded, out_w, out_h)
                    assert prepared is not None
                    writer.write(prepared)
        except (UnreadableVideoError, OSError) as exc:
            # Source errors were converted to _SourceFailure above, so what is left here came
            # from the writer: the destination, not the source, is at fault.
            raise _DestinationFailure(exc) from exc


def _fail_destination(
    exc: _DestinationFailure | OSError, activity: ActivityLog, session_id: str
) -> TeamsAvatarError:
    """Log a destination failure and return the error to raise (storage full or write error)."""
    cause = exc.cause if isinstance(exc, _DestinationFailure) else exc
    if _is_disk_full(cause):
        _record_rejection(
            activity, "the library could not be written: the disk is full", session_id
        )
        return StorageFullError()
    reason = cause.reason if isinstance(cause, UnreadableVideoError) else str(cause)
    _record_rejection(activity, f"the library could not be written ({reason})", session_id)
    return LibraryWriteError(reason)


def import_file(
    paths: DataPaths,
    library: Library,
    activity: ActivityLog,
    source: Path,
    name: str | None,
    now: Callable[[], datetime] = datetime.now,
) -> RecordResult:
    """Import ``source`` as a new recording and return it with its warnings.

    Args:
        paths: Data folders.
        library: Library the recording is added to.
        activity: Activity log for the ``imported`` / ``import_rejected`` records.
        source: Video file to import; never modified.
        name: Recording name, or ``None`` for the default name built from ``now()``.
        now: Local-time clock, injectable for tests.

    Returns:
        The stored recording and its warnings.

    Raises:
        InvalidArgumentError: If ``name`` breaks the name rules.
        NameClashError: If ``name`` is already used (checked before any encoding).
        StorageFullError: If less than 200 MB of disk is free, or the disk fills up meanwhile.
        ImportRejectedError: If the source is missing, not a video, truncated or undecodable.
        LibraryWriteError: If the recording cannot be written for a reason other than space.
    """
    chosen = library.check_name_free(name if name is not None else default_name(now()))
    # The folder may not exist on a first run, and disk_usage needs an existing path.
    paths.recordings.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(paths.recordings).free < MIN_FREE_BYTES:
        raise StorageFullError()

    recording_id = uuid.uuid4().hex
    partial = paths.partial_file(recording_id)
    final = paths.recording_file(recording_id)
    with logging_setup.session(recording_id):
        logger.info("import started")
        try:
            _transcode(source, partial)
            os.replace(partial, final)
        except _SourceFailure as exc:
            partial.unlink(missing_ok=True)
            _record_rejection(activity, exc.reason, recording_id)
            raise ImportRejectedError(source.name, exc.reason) from exc
        except (_DestinationFailure, OSError) as exc:
            partial.unlink(missing_ok=True)
            raise _fail_destination(exc, activity, recording_id) from exc
        except BaseException:
            # Anything else (a bug, Ctrl+C) must not leave a half-written recording behind.
            partial.unlink(missing_ok=True)
            raise
        try:
            info = probe(final)
            recording = Recording(
                id=recording_id,
                name=chosen,
                origin="imported",
                created_at=datetime.now(UTC),
                duration_s=info.duration_s,
                frame_count=info.frame_count,
                width=info.width,
                height=info.height,
                size_bytes=final.stat().st_size,
                warnings=analysis.compute_warnings(final),
                source_name=source.name,
            )
            recording = library.register(recording)
        except UnreadableVideoError as exc:
            final.unlink(missing_ok=True)
            _record_rejection(activity, exc.reason, recording_id)
            raise ImportRejectedError(source.name, exc.reason) from exc
        except OSError as exc:
            final.unlink(missing_ok=True)
            raise _fail_destination(exc, activity, recording_id) from exc
        except BaseException:
            final.unlink(missing_ok=True)
            raise
        try:
            activity.append(
                ActivityRecord(
                    at=recording.created_at,
                    event="imported",
                    recording_id=recording.id,
                    recording_name=recording.name,
                    session_id=recording_id,
                    outcome="saved",
                )
            )
        except Exception:
            # The recording is already stored; a broken log must not turn that into a failure.
            logger.warning("could not write the activity record", exc_info=True)
        logger.info("import finished: %d frames", recording.frame_count)
    return RecordResult(recording, list(recording.warnings))
