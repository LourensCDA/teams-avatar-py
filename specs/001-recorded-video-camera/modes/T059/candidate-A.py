"""Record from the camera into the library (FR-001, FR-003, FR-004, FR-015; research R3, R8).

A session goes ``starting -> recording -> (saved | cancelled | failed)``. The camera is opened
only after the name and free space are checked, so a refusal never touches the device.
"""

from __future__ import annotations

import errno
import logging
import os
import shutil
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from teams_avatar import analysis, logging_setup
from teams_avatar.activity import ActivityLog
from teams_avatar.adapters.pyav_codec import Frame, PyAVWriter, probe
from teams_avatar.errors import (
    CameraUnavailableError,
    LibraryWriteError,
    StorageFullError,
    TeamsAvatarError,
    UnreadableVideoError,
)
from teams_avatar.library import Library, default_name
from teams_avatar.models import ActivityRecord, CameraInfo, Recording, RecordResult
from teams_avatar.paths import DataPaths

logger = logging.getLogger(__name__)

MIN_FREE_BYTES = 200 * 1024 * 1024
# One check per second of recorded video keeps disk_usage calls cheap yet reacts in time.
SPACE_CHECK_EVERY_FRAMES = 30
OUTPUT_FPS = 30

Key = Literal["save", "cancel"]
Outcome = Literal["saved", "cancelled", "failed"]


class CameraSource(Protocol):
    """A physical camera that yields BGR frames (the real one is in ``adapters``)."""

    @property
    def size(self) -> tuple[int, int]:
        """Return the opened capture size as ``(width, height)``."""
        ...

    def open(self, camera: CameraInfo) -> None:
        """Open ``camera`` or raise ``CameraUnavailableError``."""
        ...

    def read(self) -> Frame:
        """Return the next frame or raise ``CameraUnavailableError``."""
        ...

    def close(self) -> None:
        """Release the camera."""
        ...


class RecordingUI(Protocol):
    """The live preview window shown while recording."""

    def show(self, frame: Frame, elapsed_s: float) -> None:
        """Draw ``frame`` with the REC mark and elapsed time."""
        ...

    def poll_key(self) -> str | None:
        """Return "save" or "cancel" if pressed since the last call, else None."""
        ...

    def window_closed(self) -> bool:
        """Return True once the user closed the window."""
        ...

    def close(self) -> None:
        """Destroy the window."""
        ...


def _disk_free(path: Path) -> int:
    """Return the free bytes on the volume holding ``path``."""
    return shutil.disk_usage(path).free


class _Stop(Exception):
    """Internal: the user cancelled the recording."""


class RecordingSession:
    """One recording from the camera into the library."""

    def __init__(
        self,
        paths: DataPaths,
        library: Library,
        activity: ActivityLog,
        source: CameraSource,
        *,
        clock: Callable[[], float] | None = None,
        free_bytes: Callable[[Path], int] = _disk_free,
        now: Callable[[], datetime] = datetime.now,
        frame_interval_s: float = 1 / OUTPUT_FPS,
    ) -> None:
        """Bind the collaborators; ``clock``, ``free_bytes`` and ``now`` are test seams."""
        self._paths = paths
        self._library = library
        self._activity = activity
        self._source = source
        self._clock = clock
        self._free_bytes = free_bytes
        self._now = now
        self._interval = frame_interval_s

    def run(self, name: str | None, camera: CameraInfo, ui: RecordingUI) -> RecordResult | None:
        """Record until the user saves or cancels.

        Args:
            name: Recording name, or ``None`` for the default name built from ``now()``.
            camera: Camera to record from.
            ui: Preview window; always closed before returning.

        Returns:
            The saved recording and its warnings, or ``None`` if the user cancelled.

        Raises:
            InvalidArgumentError: If ``name`` breaks the name rules.
            NameClashError: If ``name`` is already used (camera not opened).
            StorageFullError: If less than 200 MB is free before starting.
            CameraUnavailableError: If the camera cannot be opened or fails while recording.
            LibraryWriteError: If the recording cannot be written.
        """
        session_id = uuid.uuid4().hex
        try:
            chosen = self._library.check_name_free(
                name if name is not None else default_name(self._now())
            )
            self._paths.recordings.mkdir(parents=True, exist_ok=True)
            if self._free_bytes(self._paths.recordings) < MIN_FREE_BYTES:
                raise StorageFullError()
            with logging_setup.session(session_id):
                return self._record(chosen, camera, ui, session_id)
        finally:
            ui.close()

    def _record(
        self, name: str, camera: CameraInfo, ui: RecordingUI, session_id: str
    ) -> RecordResult | None:
        """Open the camera, record, and finish the session (camera closed on every path)."""
        recording_id = uuid.uuid4().hex
        partial = self._paths.partial_file(recording_id)
        try:
            try:
                self._source.open(camera)
            except CameraUnavailableError as exc:
                self._log(session_id, "failed", exc.message)
                raise
            logger.info("recording started")
            try:
                frames = self._capture(partial, ui)
            except _Stop:
                partial.unlink(missing_ok=True)
                self._log(session_id, "cancelled", None)
                return None
            except CameraUnavailableError as exc:
                partial.unlink(missing_ok=True)
                self._log(session_id, "failed", exc.message)
                raise
            except (OSError, UnreadableVideoError) as exc:
                partial.unlink(missing_ok=True)
                error = self._write_error(exc)
                self._log(session_id, "failed", error.message)
                raise error from exc
            return self._save(recording_id, name, partial, session_id, frames)
        except BaseException:
            # Covers Ctrl+C and bugs: a half-written recording must never stay behind.
            partial.unlink(missing_ok=True)
            raise
        finally:
            self._source.close()

    def _capture(self, partial: Path, ui: RecordingUI) -> int:
        """Write frames to ``partial`` until save, low disk space, or raise ``_Stop``.

        Returns:
            The number of frames written.
        """
        width, height = self._source.size
        count = 0
        with PyAVWriter(partial, width, height, OUTPUT_FPS) as writer:
            while True:
                frame = self._source.read()
                writer.write(frame)
                count += 1
                ui.show(frame, (count - 1) * self._interval)
                key = ui.poll_key()
                if ui.window_closed() or key == "cancel":
                    raise _Stop
                if key == "save":
                    return count
                if count % SPACE_CHECK_EVERY_FRAMES == 0 and (
                    self._free_bytes(self._paths.recordings) < MIN_FREE_BYTES
                ):
                    # Keep what exists as a valid shorter recording rather than losing it.
                    logger.warning("free space low; stopping the recording early")
                    return count

    def _save(
        self, recording_id: str, name: str, partial: Path, session_id: str, frames: int
    ) -> RecordResult:
        """Publish the partial file and add it to the library."""
        final = self._paths.recording_file(recording_id)
        try:
            os.replace(partial, final)
            try:
                info = probe(final)
                recording = Recording(
                    id=recording_id,
                    name=name,
                    origin="recorded",
                    created_at=datetime.now(UTC),
                    duration_s=info.duration_s,
                    frame_count=info.frame_count,
                    width=info.width,
                    height=info.height,
                    size_bytes=final.stat().st_size,
                    warnings=analysis.compute_warnings(final),
                )
                self._library.add(recording)
            except BaseException:
                final.unlink(missing_ok=True)
                raise
        except (OSError, UnreadableVideoError) as exc:
            error = self._write_error(exc)
            self._log(session_id, "failed", error.message)
            raise error from exc
        self._activity.append(
            ActivityRecord(
                at=recording.created_at,
                event="recorded",
                recording_id=recording.id,
                recording_name=recording.name,
                session_id=session_id,
                outcome="saved",
            )
        )
        logger.info("recording saved: %d frames", frames)
        return RecordResult(recording, list(recording.warnings))

    @staticmethod
    def _write_error(exc: OSError | UnreadableVideoError) -> TeamsAvatarError:
        """Map a write failure to a user-facing error (disk full gets its own message)."""
        if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
            return StorageFullError()
        reason = exc.reason if isinstance(exc, UnreadableVideoError) else str(exc)
        return LibraryWriteError(reason)

    def _log(self, session_id: str, outcome: Outcome, detail: str | None) -> None:
        """Append a ``record_failed`` activity record for a cancelled or failed session."""
        logger.info("recording %s", outcome)
        self._activity.append(
            ActivityRecord(
                at=datetime.now(UTC),
                event="record_failed",
                recording_id=None,
                recording_name=None,
                session_id=session_id,
                outcome=outcome,
                detail=detail,
            )
        )
