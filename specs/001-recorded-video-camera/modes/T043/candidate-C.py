"""Import an existing video into the library (FR-021, FR-022; research R3, R6, R8).

The source is only ever read. It is decoded frame by frame, resampled to 30 fps by presentation
time, capped in size and re-encoded video-only into ``recordings/<id>.mp4``.
"""

from __future__ import annotations

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
from teams_avatar.errors import ImportRejectedError, StorageFullError, UnreadableVideoError
from teams_avatar.library import Library, default_name
from teams_avatar.models import ActivityRecord, Recording, RecordResult
from teams_avatar.paths import DataPaths

logger = logging.getLogger(__name__)

OUTPUT_FPS = 30
MIN_FREE_BYTES = 200 * 1024 * 1024


def _reject(
    activity: ActivityLog, source: Path, reason: str, session_id: str
) -> ImportRejectedError:
    """Log the rejection to the activity log and return the error for the caller to raise."""
    logger.info("import rejected: %s", reason)
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
    return ImportRejectedError(source.name, reason)


def _plain(exc: UnreadableVideoError) -> str:
    """Return the codec's reason without the generic "The video cannot be read:" lead-in."""
    return exc.cause.removeprefix("The video cannot be read: ")


def _transcode(source: Path, partial: Path) -> None:
    """Decode ``source`` and write it to ``partial`` at 30 fps, capped in size, video only.

    Output frame ``k`` shows the latest source frame at or before time ``k / 30``. Choosing by
    time rather than by index keeps the duration the same for any source frame rate.

    Raises:
        UnreadableVideoError: If the source cannot be fully decoded or the output cannot be written.
    """
    with PyAVReader(source) as reader:
        out_w, out_h = frames.cap_size(reader.width, reader.height)
        resize = (out_w, out_h) != (reader.width, reader.height)
        total = max(1, round(reader.frame_count / reader.fps * OUTPUT_FPS))
        current: Frame | None = None
        current_index = -1
        prepared: Frame | None = None
        with PyAVWriter(partial, out_w, out_h, OUTPUT_FPS) as writer:
            for k in range(total):
                # The epsilon stops float error from skipping a frame when rates divide evenly.
                wanted = min(reader.frame_count - 1, int(k * reader.fps / OUTPUT_FPS + 1e-6))
                if wanted != current_index:
                    while current_index < wanted:
                        current = reader.read()
                        current_index += 1
                        if current is None:
                            raise UnreadableVideoError("the video ends before its last frame")
                    assert current is not None
                    # INTER_AREA is the sharpest option for shrinking; never used to enlarge.
                    prepared = (
                        np.asarray(
                            cv2.resize(current, (out_w, out_h), interpolation=cv2.INTER_AREA),
                            dtype=np.uint8,
                        )
                        if resize
                        else current
                    )
                assert prepared is not None
                writer.write(prepared)


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
        StorageFullError: If less than 200 MB of disk is free.
        ImportRejectedError: If the source is missing, not a video, truncated or undecodable.
    """
    chosen = library.check_name_free(name if name is not None else default_name(now()))
    if shutil.disk_usage(paths.recordings).free < MIN_FREE_BYTES:
        raise StorageFullError()

    recording_id = uuid.uuid4().hex
    session_id = recording_id
    partial = paths.partial_file(recording_id)
    final = paths.recording_file(recording_id)
    with logging_setup.session(session_id):
        logger.info("import started")
        try:
            _transcode(source, partial)
            os.replace(partial, final)
        except UnreadableVideoError as exc:
            partial.unlink(missing_ok=True)
            raise _reject(activity, source, _plain(exc), session_id) from exc
        except BaseException:
            # Anything else (disk error, Ctrl+C) must not leave a half-written recording behind.
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
            library.add(recording)
        except BaseException:
            final.unlink(missing_ok=True)
            raise
        activity.append(
            ActivityRecord(
                at=recording.created_at,
                event="imported",
                recording_id=recording.id,
                recording_name=recording.name,
                session_id=session_id,
                outcome="saved",
            )
        )
        logger.info("import finished: %d frames", recording.frame_count)
    return RecordResult(recording, list(recording.warnings))
