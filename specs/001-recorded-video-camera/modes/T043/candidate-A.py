"""Import an existing video file into the library (FR-021, FR-022; research R3, R6, R8)."""

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

from teams_avatar import analysis
from teams_avatar.activity import ActivityLog
from teams_avatar.adapters import pyav_codec
from teams_avatar.adapters.pyav_codec import Frame, PyAVReader, PyAVWriter
from teams_avatar.errors import ImportRejectedError, StorageFullError, UnreadableVideoError
from teams_avatar.frames import cap_size
from teams_avatar.library import Library, default_name
from teams_avatar.logging_setup import session
from teams_avatar.models import ActivityRecord, Recording, RecordResult
from teams_avatar.paths import DataPaths

logger = logging.getLogger(__name__)

OUTPUT_FPS = 30
MIN_FREE_BYTES = 200 * 1024 * 1024


def _reencode(source: Path, target: Path) -> None:
    """Re-encode ``source`` to ``target`` at 30 fps, capped size, video only.

    Output frame ``k`` shows the source frame presented at or before ``k / 30`` seconds, so
    other frame rates are repeated or dropped by time and the duration is preserved.

    Raises:
        UnreadableVideoError: If the source cannot be decoded completely.
    """
    with PyAVReader(source) as reader:
        out_w, out_h = cap_size(reader.width, reader.height)
        total = max(1, round(reader.frame_count * OUTPUT_FPS / reader.fps))
        # A source frame's time is index / fps, assuming a constant source rate.
        src_index = -1
        current: Frame | None = None
        with PyAVWriter(target, out_w, out_h, OUTPUT_FPS) as writer:
            for k in range(total):
                wanted = min(int(k * reader.fps / OUTPUT_FPS + 1e-6), reader.frame_count - 1)
                while src_index < wanted:
                    current = reader.read()
                    src_index += 1
                    if current is None:
                        raise UnreadableVideoError("the video ends earlier than it should")
                assert current is not None  # loop above always reads at least once for k == 0
                if (current.shape[1], current.shape[0]) != (out_w, out_h):
                    out = np.asarray(
                        cv2.resize(current, (out_w, out_h), interpolation=cv2.INTER_AREA),
                        dtype=np.uint8,
                    )
                else:
                    out = current
                writer.write(out)


def _log_rejection(activity: ActivityLog, reason: str, session_id: str) -> None:
    """Record an ``import_rejected`` activity entry."""
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


def import_file(
    paths: DataPaths,
    library: Library,
    activity: ActivityLog,
    source: Path,
    name: str | None,
    now: Callable[[], datetime] = datetime.now,
) -> RecordResult:
    """Import ``source`` as a new library recording; the original is never modified.

    Args:
        paths: Data folder layout.
        library: Library the recording is added to.
        activity: Activity log for the ``imported`` / ``import_rejected`` record.
        source: Video file to import.
        name: Recording name, or ``None`` for the timestamp default.
        now: Returns the current local time (used for the default name).

    Returns:
        The stored recording and its warnings.

    Raises:
        InvalidArgumentError: If ``name`` breaks the name rules.
        NameClashError: If ``name`` is already used (checked before any encoding).
        StorageFullError: If less than 200 MB is free.
        ImportRejectedError: If the file is missing, not a video or cannot be fully decoded.
    """
    recording_id = uuid.uuid4().hex
    with session(recording_id):
        final_name = library.check_name_free(name) if name is not None else default_name(now())
        if shutil.disk_usage(paths.recordings).free < MIN_FREE_BYTES:
            raise StorageFullError
        partial = paths.partial_file(recording_id)
        stored = paths.recording_file(recording_id)
        logger.info("import started")
        try:
            _reencode(source, partial)
            os.replace(partial, stored)
            info = pyav_codec.probe(stored)
            warnings = analysis.compute_warnings(stored)
        except UnreadableVideoError as exc:
            _discard(partial, stored)
            reason = exc.cause
            logger.warning("import rejected: %s", reason)
            _log_rejection(activity, reason, recording_id)
            raise ImportRejectedError(source.name, reason) from exc
        except BaseException:
            _discard(partial, stored)
            raise
        recording = Recording(
            id=recording_id,
            name=final_name,
            origin="imported",
            created_at=datetime.now(UTC),
            duration_s=info.duration_s,
            frame_count=info.frame_count,
            width=info.width,
            height=info.height,
            size_bytes=stored.stat().st_size,
            warnings=warnings,
            source_name=source.name,
        )
        try:
            library.add(recording)
        except BaseException:
            _discard(partial, stored)
            raise
        activity.append(
            ActivityRecord(
                at=recording.created_at,
                event="imported",
                recording_id=recording_id,
                recording_name=final_name,
                session_id=recording_id,
                outcome="saved",
            )
        )
        logger.info("import saved: %d frames", info.frame_count)
        return RecordResult(recording, warnings)


def _discard(*files: Path) -> None:
    """Delete leftover files after a failed import; missing files are fine."""
    for file in files:
        file.unlink(missing_ok=True)
