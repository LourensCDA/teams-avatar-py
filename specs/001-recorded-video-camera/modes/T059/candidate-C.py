"""Record the user's camera into the library (FR-001, FR-003, FR-004, FR-015; research R3, R8).

Transitions: ``starting -> recording -> (saved | cancelled | failed)``; ``starting -> failed``
when the camera is unavailable. The file is written as ``<id>.partial.mp4`` and renamed only on
success, so a crash or cancel never leaves a half recording that looks valid.
"""

from __future__ import annotations

import logging
import os
import shutil
import uuid
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from teams_avatar import analysis, logging_setup
from teams_avatar.activity import ActivityLog
from teams_avatar.adapters.pyav_codec import Frame, PyAVWriter, probe
from teams_avatar.errors import CameraUnavailableError, StorageFullError
from teams_avatar.library import Library, default_name
from teams_avatar.models import ActivityRecord, CameraInfo, Recording, RecordResult
from teams_avatar.paths import DataPaths

logger = logging.getLogger(__name__)

MIN_FREE_BYTES = 200 * 1024 * 1024
FPS = 30
# Roughly once per second of recorded video.
_SPACE_CHECK_EVERY = FPS


class CameraSourceLike(Protocol):
    """The slice of the camera adapter a recording needs."""

    @property
    def size(self) -> tuple[int, int]:
        """Return the opened capture size as ``(width, height)``."""
        ...

    def open(self, camera: CameraInfo) -> None:
        """Open ``camera`` or raise ``CameraUnavailableError``."""
        ...

    def read(self) -> Frame:
        """Return the next BGR frame or raise ``CameraUnavailableError``."""
        ...

    def close(self) -> None:
        """Release the camera."""
        ...


class RecordingUI(Protocol):
    """The recording window: live view with a "REC" mark and elapsed time."""

    def show(self, frame: Frame, elapsed_s: float) -> None:
        """Draw ``frame`` with the elapsed recording time."""
        ...

    def poll_key(self) -> str | None:
        """Return "save" or "cancel" when the user chose, else None."""
        ...

    def window_closed(self) -> bool:
        """Report whether the user closed the window."""
        ...

    def close(self) -> None:
        """Close the window."""
        ...


def _disk_free(path: Path) -> int:
    """Return the free bytes on the volume holding ``path``."""
    return shutil.disk_usage(path).free


class RecordingSession:
    """One recording from camera open to saved, cancelled or failed."""

    def __init__(
        self,
        paths: DataPaths,
        library: Library,
        activity: ActivityLog,
        source: CameraSourceLike,
        *,
        clock: Callable[[], float],
        free_bytes: Callable[[Path], int] = _disk_free,
        now: Callable[[], datetime] = datetime.now,
        frame_interval_s: float = 1 / FPS,
    ) -> None:
        """Bind the session to its data folder, camera and injectable time/space sources."""
        self.paths = paths
        self.library = library
        self.activity = activity
        self.source = source
        self.clock = clock
        self.free_bytes = free_bytes
        self.now = now
        self.frame_interval_s = frame_interval_s

    def run(self, name: str | None, camera: CameraInfo, ui: RecordingUI) -> RecordResult | None:
        """Record until the user saves or cancels.

        Args:
            name: Wanted recording name, or ``None`` for the timestamp default.
            camera: Camera to record from.
            ui: Recording window; always closed, as is the camera.

        Returns:
            The saved recording, or ``None`` if the user cancelled.

        Raises:
            InvalidArgumentError: If the name breaks the name rules.
            NameClashError: If the name is taken (nothing is opened).
            StorageFullError: If free space is below the minimum before starting.
            CameraUnavailableError: If the camera cannot be opened or fails mid-recording.
        """
        session_id = uuid.uuid4().hex
        recording_id = uuid.uuid4().hex
        try:
            with logging_setup.session(session_id):
                chosen = self.library.check_name_free(name) if name is not None else None
                if self.free_bytes(self.paths.recordings) < MIN_FREE_BYTES:
                    raise StorageFullError()
                return self._record(chosen, camera, ui, session_id, recording_id)
        finally:
            with suppress(Exception):
                self.source.close()
            with suppress(Exception):
                ui.close()

    def _record(
        self,
        chosen: str | None,
        camera: CameraInfo,
        ui: RecordingUI,
        session_id: str,
        recording_id: str,
    ) -> RecordResult | None:
        """Open the camera, capture frames, then save or discard (see ``run``)."""
        partial = self.paths.partial_file(recording_id)
        final = self.paths.recording_file(recording_id)
        try:
            try:
                self.source.open(camera)
            except CameraUnavailableError as exc:
                self._log_end("failed", session_id, chosen, str(exc))
                raise
            logger.info("recording started", extra={"camera": camera.name})
            outcome, frames = self._capture(partial, ui, session_id, chosen)
            if outcome == "cancelled":
                partial.unlink(missing_ok=True)
                self._log_end("cancelled", session_id, chosen, "the recording was cancelled")
                return None
            os.replace(partial, final)
            return self._register(final, recording_id, chosen, session_id, frames)
        except BaseException:
            # Covers errors and KeyboardInterrupt: nothing half-written may stay behind.
            partial.unlink(missing_ok=True)
            final.unlink(missing_ok=True)
            raise

    def _capture(
        self, partial: Path, ui: RecordingUI, session_id: str, chosen: str | None
    ) -> tuple[Literal["saved", "cancelled"], int]:
        """Write frames to ``partial`` until save, cancel or low space; return the outcome."""
        width, height = self.source.size
        writer = PyAVWriter(partial, width, height, FPS)
        count = 0
        outcome: Literal["saved", "cancelled"] = "saved"
        try:
            while True:
                try:
                    frame = self.source.read()
                except CameraUnavailableError as exc:
                    self._log_end("failed", session_id, chosen, str(exc))
                    raise
                writer.write(frame)
                ui.show(frame, count * self.frame_interval_s)
                count += 1
                key = ui.poll_key()
                if key == "save":
                    break
                if key == "cancel" or ui.window_closed():
                    outcome = "cancelled"
                    break
                if (
                    count % _SPACE_CHECK_EVERY == 0
                    and self.free_bytes(self.paths.recordings) < MIN_FREE_BYTES
                ):
                    logger.warning("disk space low, saving the shorter recording")
                    break
            writer.close()
        except BaseException:
            # Releases the file handle so the caller can delete the partial on Windows.
            with suppress(Exception):
                writer.close()
            raise
        return outcome, count

    def _register(
        self,
        final: Path,
        recording_id: str,
        chosen: str | None,
        session_id: str,
        frames: int,
    ) -> RecordResult:
        """Probe the saved file, add it to the library and log it."""
        info = probe(final)
        name = chosen or default_name(self.now())
        warnings = analysis.compute_warnings(final)
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
            warnings=warnings,
        )
        self.library.add(recording)
        self.activity.append(
            ActivityRecord(
                at=datetime.now(UTC),
                event="recorded",
                recording_id=recording_id,
                recording_name=name,
                session_id=session_id,
                outcome="saved",
            )
        )
        logger.info("recording saved", extra={"frames": frames})
        return RecordResult(recording=recording, warnings=warnings)

    def _log_end(self, outcome: str, session_id: str, name: str | None, detail: str) -> None:
        """Append a ``record_failed`` activity record for a recording that was not saved."""
        self.activity.append(
            ActivityRecord(
                at=datetime.now(UTC),
                event="record_failed",
                recording_id=None,
                recording_name=name,
                session_id=session_id,
                outcome=outcome,
                detail=detail,
            )
        )


__all__ = ["MIN_FREE_BYTES", "CameraSourceLike", "RecordingSession", "RecordingUI"]
