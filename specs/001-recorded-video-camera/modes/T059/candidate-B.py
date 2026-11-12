"""Record a camera into the library (FR-001, FR-003, FR-004, FR-015; research R3, R8).

State flow: ``starting -> recording -> (saved | cancelled | failed)``. The file is written as
``<id>.partial.mp4`` and renamed only on success, so a crash never leaves a half recording that
looks valid.
"""

from __future__ import annotations

import contextlib
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
    UnreadableVideoError,
)
from teams_avatar.library import Library, default_name
from teams_avatar.models import ActivityRecord, CameraInfo, Recording, RecordResult
from teams_avatar.paths import DataPaths

logger = logging.getLogger(__name__)

MIN_FREE_BYTES = 200 * 1024 * 1024
OUTPUT_FPS = 30
# About once per second of recorded video; disk_usage is cheap but not free.
_SPACE_CHECK_EVERY = OUTPUT_FPS

_Ending = Literal["save", "cancel"]


class CameraSource(Protocol):
    """A camera that can be opened, read frame by frame and closed."""

    @property
    def size(self) -> tuple[int, int]:
        """Return the opened capture size as (width, height)."""
        ...

    def open(self, camera: CameraInfo) -> None:
        """Open ``camera`` or raise ``CameraUnavailableError``."""
        ...

    def read(self) -> Frame:
        """Return the next BGR frame or raise ``CameraUnavailableError``."""
        ...

    def close(self) -> None:
        """Release the camera; safe to call more than once."""
        ...


class RecordingUI(Protocol):
    """The live preview window; it draws the REC mark and elapsed time itself."""

    def show(self, frame: Frame, elapsed_s: float) -> None:
        """Display ``frame`` with the elapsed recording time."""
        ...

    def poll_key(self) -> str | None:
        """Return ``"save"`` or ``"cancel"`` if the user pressed one, else ``None``."""
        ...

    def window_closed(self) -> bool:
        """Return True once the user closed the window."""
        ...

    def close(self) -> None:
        """Destroy the window; safe to call more than once."""
        ...


class RecordingSession:
    """One interactive recording from a camera into the library."""

    def __init__(
        self,
        paths: DataPaths,
        library: Library,
        activity: ActivityLog,
        source: CameraSource,
        *,
        clock: Callable[[], float],
        free_bytes: Callable[[Path], int] | None = None,
        now: Callable[[], datetime] = datetime.now,
        frame_interval_s: float = 1 / OUTPUT_FPS,
    ) -> None:
        """Bind collaborators; nothing is opened until ``run``.

        Args:
            paths: Data folders.
            library: Library the finished recording is added to.
            activity: Activity log for ``recorded`` / ``record_failed`` records.
            source: Camera to read from.
            clock: Monotonic seconds, used only to report wall-clock duration in logs.
            free_bytes: Returns free bytes for a folder; defaults to ``shutil.disk_usage``.
            now: Local-time clock used for the default name.
            frame_interval_s: Video time one frame adds; drives the elapsed display.
        """
        self._paths = paths
        self._library = library
        self._activity = activity
        self._source = source
        self._clock = clock
        self._free_bytes = free_bytes or (lambda p: shutil.disk_usage(p).free)
        self._now = now
        self._interval = frame_interval_s

    def run(self, name: str | None, camera: CameraInfo, ui: RecordingUI) -> RecordResult | None:
        """Record until the user saves or cancels.

        Args:
            name: Recording name, or ``None`` for the default timestamp name.
            camera: Camera to record from.
            ui: Preview window and key source.

        Returns:
            The saved recording, or ``None`` if the user cancelled.

        Raises:
            InvalidArgumentError: If ``name`` breaks the name rules.
            NameClashError: If ``name`` is taken (nothing is opened).
            StorageFullError: If free space is below the minimum or the disk fills up.
            CameraUnavailableError: If the camera cannot be opened or fails mid-recording.
            LibraryWriteError: If the recording cannot be written for another reason.
        """
        # The UI is closed on every path, including the early refusals below.
        try:
            chosen = self._library.check_name_free(
                name if name is not None else default_name(self._now())
            )
            self._paths.recordings.mkdir(parents=True, exist_ok=True)
            if self._free_bytes(self._paths.recordings) < MIN_FREE_BYTES:
                raise StorageFullError()
            session_id = uuid.uuid4().hex
            with logging_setup.session(session_id):
                return self._record(chosen, camera, ui, session_id)
        finally:
            ui.close()

    def _record(
        self, name: str, camera: CameraInfo, ui: RecordingUI, session_id: str
    ) -> RecordResult | None:
        """Open the camera, record into a partial file and finish or discard it."""
        recording_id = uuid.uuid4().hex
        partial = self._paths.partial_file(recording_id)
        started = self._clock()
        try:
            try:
                self._source.open(camera)
            except CameraUnavailableError as exc:
                self._log(session_id, "record_failed", "failed", None, exc.message)
                raise
            logger.info("recording started")
            frames = self._capture(partial, ui, session_id)
            if frames is None:
                partial.unlink(missing_ok=True)
                self._log(session_id, "record_failed", "cancelled", None, "cancelled by user")
                return None
            result = self._finalise(partial, recording_id, name, session_id)
            logger.info("recording saved: %d frames in %.1fs", frames, self._clock() - started)
            return result
        except KeyboardInterrupt:
            partial.unlink(missing_ok=True)
            self._log(session_id, "record_failed", "cancelled", None, "interrupted")
            raise
        except BaseException:
            # Covers writer, probe and library failures: never leave the partial behind.
            partial.unlink(missing_ok=True)
            raise
        finally:
            self._source.close()

    def _capture(self, partial: Path, ui: RecordingUI, session_id: str) -> int | None:
        """Write frames to ``partial``; return the frame count, or ``None`` if cancelled."""
        width, height = self._source.size
        count = 0
        ending: _Ending = "save"
        try:
            with PyAVWriter(partial, width, height, OUTPUT_FPS) as writer:
                while True:
                    frame = self._source.read()
                    writer.write(frame)
                    ui.show(frame, count * self._interval)
                    count += 1
                    requested = self._requested_ending(ui)
                    if requested is not None:
                        ending = requested
                        break
                    if (
                        count % _SPACE_CHECK_EVERY == 0
                        and self._free_bytes(self._paths.recordings) < MIN_FREE_BYTES
                    ):
                        # Keep what exists: a shorter valid recording beats losing it all.
                        logger.warning("disk space low; saving shorter recording")
                        break
        except CameraUnavailableError as exc:
            self._log(session_id, "record_failed", "failed", None, exc.message)
            raise
        except (OSError, UnreadableVideoError) as exc:
            self._log(session_id, "record_failed", "failed", None, f"could not write: {exc}")
            if _is_disk_full(exc):
                raise StorageFullError() from exc
            raise LibraryWriteError(str(exc)) from exc
        return count if ending == "save" else None

    @staticmethod
    def _requested_ending(ui: RecordingUI) -> _Ending | None:
        """Translate a key press or window close into an ending, if any."""
        if ui.window_closed():
            return "cancel"
        key = ui.poll_key()
        if key == "save":
            return "save"
        if key == "cancel":
            return "cancel"
        return None

    def _finalise(
        self, partial: Path, recording_id: str, name: str, session_id: str
    ) -> RecordResult:
        """Promote the finished partial file and add it to the library."""
        final = self._paths.recording_file(recording_id)
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
        self._log(session_id, "recorded", "saved", recording, None)
        return RecordResult(recording, list(recording.warnings))

    def _log(
        self,
        session_id: str,
        event: str,
        outcome: str,
        recording: Recording | None,
        detail: str | None,
    ) -> None:
        """Append an activity record; a logging failure must not mask the real outcome."""
        with contextlib.suppress(OSError):
            self._activity.append(
                ActivityRecord(
                    at=datetime.now(UTC),
                    event=event,
                    recording_id=recording.id if recording else None,
                    recording_name=recording.name if recording else None,
                    session_id=session_id,
                    outcome=outcome,
                    detail=detail,
                )
            )


def _is_disk_full(exc: BaseException | None) -> bool:
    """Return True if ``exc`` or its cause chain reports ENOSPC."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if getattr(exc, "errno", None) == errno.ENOSPC:
            return True
        exc = exc.__cause__ or exc.__context__
    return False
