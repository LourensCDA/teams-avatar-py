"""Import an existing video file into the library (FR-021, FR-022, research R3, R6, R8).

The source is decoded, capped in size, resampled to 30 fps by presentation time and re-encoded
into the data folder; the original file is only ever read.
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

from teams_avatar import library as library_mod
from teams_avatar.activity import ActivityLog
from teams_avatar.adapters.pyav_codec import Frame, PyAVReader, PyAVWriter, probe
from teams_avatar.analysis import compute_warnings
from teams_avatar.errors import ImportRejectedError, StorageFullError, UnreadableVideoError
from teams_avatar.frames import cap_size
from teams_avatar.library import Library
from teams_avatar.logging_setup import session
from teams_avatar.models import ActivityRecord, Recording, RecordResult
from teams_avatar.paths import DataPaths

logger = logging.getLogger(__name__)

OUTPUT_FPS = 30
MIN_FREE_BYTES = 200 * 1024 * 1024
# Absorbs float error so an exact frame boundary (e.g. 3 / 30 * 30) is not floored down a frame.
_EPS = 1e-6


def _scaled(frame: Frame, width: int, height: int) -> Frame:
    """Return ``frame`` shrunk to ``width`` x ``height`` (a no-op when already that size)."""
    if frame.shape[1] == width and frame.shape[0] == height:
        return frame
    return np.asarray(
        cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA), dtype=np.uint8
    )


def _encode(source: Path, target: Path) -> None:
    """Decode ``source`` and write it to ``target`` at 30 fps, capped in size, video only.

    Output frame ``k`` shows the source frame that is on screen at ``k / 30`` seconds, so frames
    are repeated or dropped by presentation time and the duration is preserved.

    Raises:
        UnreadableVideoError: If the source cannot be fully decoded or the target not written.
    """
    with PyAVReader(source) as reader:
        out_w, out_h = cap_size(reader.width, reader.height)
        total = round(reader.frame_count / reader.fps * OUTPUT_FPS)
        current: Frame | None = None
        current_index = -1
        with PyAVWriter(target, out_w, out_h, OUTPUT_FPS) as writer:
            for k in range(total):
                wanted = min(int(k / OUTPUT_FPS * reader.fps + _EPS), reader.frame_count - 1)
                while current_index < wanted:
                    current = reader.read()
                    if current is None:
                        raise UnreadableVideoError("the video ends earlier than expected")
                    current_index += 1
                    scaled = _scaled(current, out_w, out_h)
                if current_index != wanted or current is None:
                    raise UnreadableVideoError("the video frames are out of order")
                writer.write(scaled)


def _reject(
    activity: ActivityLog, session_id: str, source: Path, reason: str, name: str | None
) -> ImportRejectedError:
    """Log the rejection to the activity log and return the error to raise."""
    logger.warning("import rejected: %s", reason)
    activity.append(
        ActivityRecord(
            at=datetime.now(UTC),
            event="import_rejected",
            recording_id=None,
            recording_name=name,
            session_id=session_id,
            outcome="rejected",
            detail=reason,
        )
    )
    return ImportRejectedError(source.name, reason)


def import_file(
    paths: DataPaths,
    library: Library,
    activity: ActivityLog,
    source: Path,
    name: str | None,
    now: Callable[[], datetime] = datetime.now,
) -> RecordResult:
    """Validate, re-encode and add a video file to the library.

    Args:
        paths: Data folder locations.
        library: Library to add the recording to.
        activity: Activity log that receives the ``imported`` or ``import_rejected`` record.
        source: Video file to import; never modified.
        name: Recording name, or ``None`` for the timestamp default.
        now: Returns the current local time (used only for the default name).

    Returns:
        The stored recording and its save-time warnings.

    Raises:
        InvalidArgumentError: If ``name`` breaks the name rules.
        NameClashError: If ``name`` is already used.
        StorageFullError: If less than 200 MB of disk is free.
        ImportRejectedError: If the file is missing, not a video, truncated or undecodable.
    """
    # Name and disk checks come first so a clash or full disk costs no encoding time.
    chosen = library.check_name_free(name) if name is not None else library_mod.default_name(now())
    paths.recordings.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(paths.recordings).free < MIN_FREE_BYTES:
        raise StorageFullError

    recording_id = uuid.uuid4().hex
    partial = paths.partial_file(recording_id)
    final = paths.recording_file(recording_id)
    with session(recording_id):
        logger.info("import started")
        try:
            _encode(source, partial)
            os.replace(partial, final)
            info = probe(final)
            warnings = compute_warnings(final)
        except UnreadableVideoError as exc:
            _discard(partial, final)
            raise _reject(activity, recording_id, source, exc.cause, chosen) from exc
        except BaseException:
            _discard(partial, final)
            raise
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
            warnings=warnings,
            source_name=source.name,
        )
        try:
            library.add(recording)
        except BaseException:
            _discard(partial, final)
            raise
        activity.append(
            ActivityRecord(
                at=recording.created_at,
                event="imported",
                recording_id=recording_id,
                recording_name=chosen,
                session_id=recording_id,
                outcome="saved",
            )
        )
        logger.info("import saved: %d frames", info.frame_count)
    return RecordResult(recording, list(warnings))


def _discard(*files: Path) -> None:
    """Delete leftover video files after a failed import; missing files are fine."""
    for file in files:
        file.unlink(missing_ok=True)
