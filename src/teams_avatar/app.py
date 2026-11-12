"""Application facade: the only surface front ends use (FR-019, contracts/app-api.md).

It returns plain result objects and raises ``TeamsAvatarError`` subclasses; it never prints.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn, Protocol

import numpy as np
import numpy.typing as npt

from teams_avatar import control as control_mod
from teams_avatar import importer
from teams_avatar.activity import ActivityLog
from teams_avatar.adapters import pyav_codec
from teams_avatar.control import ControlChannel
from teams_avatar.errors import (
    AlreadyPresentingError,
    CameraUnavailableError,
    InUseError,
    LibraryWriteError,
    NoDefaultError,
    RecordingUnreadableError,
    ServiceError,
    ServiceNotRunning,
    TeamsAvatarError,
    UnreadableVideoError,
    VirtualCameraError,
)
from teams_avatar.launcher import ServiceLauncher, SubprocessLauncher
from teams_avatar.library import Library
from teams_avatar.locks import ServiceLock
from teams_avatar.logging_setup import configure as configure_logging
from teams_avatar.loop_player import LoopPlayer
from teams_avatar.models import (
    ActivityRecord,
    CameraInfo,
    PresentationStatus,
    Quality,
    Recording,
    RecordingView,
    RecordResult,
    Settings,
)
from teams_avatar.paths import DataPaths
from teams_avatar.reconcile import ReconcileReport, reconcile
from teams_avatar.recording import CameraSource as FrameSource
from teams_avatar.recording import RecordingSession, RecordingUI
from teams_avatar.settings import SettingsStore

# Total budget for a present request: the service may spend up to ~9 s opening the file and
# waiting for the first picture (service.OPEN_READER_TIMEOUT_S + START_TIMEOUT_S).
PRESENT_TIMEOUT_S = 15.0
# How long shutdown waits for the service to finish its 0.5 s placeholder tail and exit.
SHUTDOWN_WAIT_S = 5.0

FIRST_USE_NOTICE = (
    "Teams Avatar shows a recording of you in place of your live camera.\n"
    'The "RECORDED VIDEO" indicator is off by default, so meeting participants will not be\n'
    "told that they are watching a recording. With the indicator off, you are responsible for\n"
    "telling participants and for following your organisation's and meeting hosts' rules.\n"
    'Turn the indicator on at any time with "teams-avatar indicator on".\n'
    "Only use recordings of yourself."
)

# Data folders already reconciled in this process: reconciliation runs once per process
# (contracts/app-api.md), even if a front end builds several App objects.
_reconciled: set[str] = set()
_reconcile_guard = threading.Lock()


log = logging.getLogger(__name__)

SERVICE_START_TIMEOUT_S = 10.0
SERVICE_POLL_INTERVAL_S = 0.1
PREVIEW_FRAME_S = 1 / 30


class CameraListing(Protocol):
    """Lists physical cameras (the real one queries Windows; tests use a fake)."""

    def list(self) -> list[CameraInfo]:
        """Return the cameras, default first."""


class PlaybackUI(Protocol):
    """Window a front end supplies for ``preview``: show frames, report Esc, close."""

    def show(self, frame: npt.NDArray[np.uint8]) -> None:
        """Show one frame."""

    def poll_key(self) -> str | None:
        """Return ``"cancel"`` after Esc, else None."""

    def window_closed(self) -> bool:
        """Return True once the user has closed the window."""

    def close(self) -> None:
        """Close the window."""


@dataclass(frozen=True)
class QualityChange:
    """Result of ``set_quality``: the saved settings and when the new value takes effect."""

    settings: Settings
    applies_at_next_service_start: bool


class App:
    """Facade over the library, settings, activity log, recorder and camera service."""

    def __init__(
        self,
        paths: DataPaths | None = None,
        *,
        control: ControlChannel | None = None,
        launcher: ServiceLauncher | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        camera_lister: CameraListing | None = None,
        camera_source: Callable[[], FrameSource] | None = None,
    ) -> None:
        """Wire the stores to a data folder and run startup reconciliation once per process.

        Args:
            paths: Data folder; defaults to ``DataPaths.resolve()``.
            control: Channel to the camera service; defaults to the per-user named pipe.
            launcher: Starts the service when it is not running; defaults to a real process.
            clock: Monotonic clock used for the service start timeout.
            sleep: Sleep function used between start-up polls.
            camera_lister: Lists physical cameras; defaults to the Windows device query.
            camera_source: Makes the capture source for ``record``; defaults to PyAV DirectShow.
        """
        self.paths = (paths or DataPaths.resolve()).ensure()
        data = self.paths
        self.control: ControlChannel = control or control_mod.ControlClient(
            control_mod.pipe_name(), lambda: control_mod.read_key(data)
        )
        self.launcher: ServiceLauncher = launcher or SubprocessLauncher(self.paths)
        self._clock = clock
        self._sleep = sleep
        self._camera_lister = camera_lister
        self._camera_source = camera_source
        configure_logging(self.paths)
        self.library = Library(self.paths)
        self.settings = SettingsStore(self.paths)
        self.activity = ActivityLog(self.paths)
        self.startup_report: ReconcileReport | None = None
        key = str(self.paths.root.resolve())
        with _reconcile_guard:
            if key not in _reconciled:
                self.startup_report = reconcile(self.paths, self.library)
                _reconciled.add(key)

    def first_use_notice(self) -> str | None:
        """Return the disclosure notice until it has been acknowledged, then ``None``."""
        if self.settings.load().first_use_acknowledged:
            return None
        return FIRST_USE_NOTICE

    def acknowledge_first_use(self) -> Settings:
        """Record that the user has read the first-use notice."""
        return self.settings.update(first_use_acknowledged=True)

    def history(self, limit: int) -> list[ActivityRecord]:
        """Return the last ``limit`` activity records, oldest first."""
        return self.activity.read_last(limit)

    def import_file(self, path: Path, name: str | None) -> RecordResult:
        """Import a video file into the library (see ``importer.import_file``)."""
        return importer.import_file(self.paths, self.library, self.activity, path, name)

    def present(self, ref: str | None) -> PresentationStatus:
        """Present a recording (the default one when ``ref`` is None) through the service.

        Starts the service when needed. Nothing in the library or settings is changed.

        Raises:
            NoDefaultError: If ``ref`` is None and no default is set.
            NotFoundError: If no recording matches ``ref``.
            RecordingUnreadableError: If the video file is missing or damaged.
            AlreadyPresentingError: If a different recording is being presented.
            VirtualCameraError: If OBS Virtual Camera is missing or in use.
            ServiceError: If the service cannot be started or fails.
        """
        recording = self._resolve(ref)
        # Checked here so a damaged file never causes a service start.
        try:
            pyav_codec.probe(self.paths.recording_file(recording.id))
        except UnreadableVideoError as exc:
            raise RecordingUnreadableError(recording.name) from exc
        settings = self.settings.load()
        message: dict[str, Any] = {
            "op": "present",
            "recording_id": recording.id,
            "name": recording.name,
            "path": str(self.paths.recording_file(recording.id)),
            "quality": settings.presentation_quality,
            "indicator": settings.indicator_on,
        }
        try:
            reply = self.control.request(message, timeout_s=PRESENT_TIMEOUT_S)
        except ServiceNotRunning:
            self._start_service()
            reply = self.control.request(message, timeout_s=PRESENT_TIMEOUT_S)
        if reply.get("ok") is not True:
            raise self._present_error(reply, recording)
        status = self._parse_status(reply)
        return dataclasses.replace(
            status,
            recording_width=recording.width,
            recording_height=recording.height,
            warnings=list(recording.warnings),
        )

    def stop(self) -> PresentationStatus:
        """Stop presenting; a no-op status when the service is not running."""
        return self._status_via({"op": "stop"})

    def status(self) -> PresentationStatus:
        """Return the service status; never raises for a missing or unresponsive service."""
        try:
            return self._status_via({"op": "status"})
        except ServiceError as exc:
            # Something holds the pipe but did not answer: report it rather than fail.
            return dataclasses.replace(self._idle_status(), last_failure=exc.message)

    def set_indicator(self, on: bool) -> PresentationStatus:
        """Save the indicator setting and apply it live if the service is running."""
        self.settings.update(indicator_on=on)
        return self._status_via({"op": "indicator", "on": on})

    def set_quality(self, quality: Quality) -> QualityChange:
        """Save the presentation quality; it only affects the next service start."""
        saved = self.settings.update(presentation_quality=quality)
        return QualityChange(saved, self._service_reachable())

    def shutdown(self) -> None:
        """Ask the service to exit and wait until it is gone; a no-op when it is not running.

        Waiting makes ``shutdown`` followed by ``present`` safe: the virtual camera is released
        only after the service's closing placeholder frames, and the service closes its pipe a
        moment before it releases ``service.lock``, so both are waited for (a new service
        started in between would lose the single-instance race and exit).

        Raises:
            ServiceError: If the service is still reachable after 5 seconds.
        """
        try:
            self.control.request({"op": "shutdown"})
        except ServiceNotRunning:
            return
        deadline = self._clock() + SHUTDOWN_WAIT_S
        while True:
            left = deadline - self._clock()
            if left <= 0:
                raise ServiceError(
                    "The camera service did not stop within 5 seconds",
                    "Close its window, then try again.",
                )
            try:
                self.control.request({"op": "status"}, timeout_s=min(1.0, left))
            except ServiceNotRunning:
                if self._service_lock_free():
                    return
            except ServiceError:
                pass  # Busy closing: keep waiting until the deadline.
            self._sleep(min(SERVICE_POLL_INTERVAL_S, max(0.0, deadline - self._clock())))

    def _service_lock_free(self) -> bool:
        """Return whether no service process holds ``service.lock`` (taken and released here)."""
        lock = ServiceLock(self.paths.service_lock)
        if not lock.try_acquire():
            return False
        lock.release()
        return True

    # --- library management (US3) -----------------------------------------------------------

    def list_recordings(self) -> list[RecordingView]:
        """Return every recording with its live ``presenting`` and ``missing`` flags."""
        presenting_id = self._presenting_id()
        return [
            RecordingView(
                recording=rec,
                presenting=rec.id == presenting_id,
                missing=not self.paths.recording_file(rec.id).exists(),
            )
            for rec in self.library.load()
        ]

    def rename(self, ref: str, new_name: str) -> Recording:
        """Rename a recording; refused while it is presenting (keeps ``status`` truthful).

        Raises:
            NotFoundError: If nothing matches ``ref``.
            InUseError: If the recording is being presented.
            NameClashError: If another recording already has ``new_name``.
        """
        target = self.library.find(ref)
        self._refuse_if_presenting(target, "rename")
        updated = self.library.rename(target.id, new_name)
        self._record("renamed", updated, "renamed", f'Renamed from "{target.name}".')
        return updated

    def delete(self, ref: str) -> None:
        """Delete a recording's file and entry (the default flag goes with the entry).

        The front end confirms first (Principle IV). The file goes first: if it cannot be
        removed nothing changes, whereas an entry removed before its file would leave an orphan
        that startup reconciliation would bring back as "Recovered".

        Raises:
            NotFoundError: If nothing matches ``ref``.
            InUseError: If the recording is being presented (FR-013).
            LibraryWriteError: If the file cannot be removed.
        """
        target = self.library.find(ref)
        self._refuse_if_presenting(target, "delete")
        try:
            self.paths.recording_file(target.id).unlink(missing_ok=True)
        except OSError as exc:
            raise LibraryWriteError(
                f'The video file of "{target.name}" could not be removed ({exc.strerror})'
            ) from exc
        self.library.remove(target.id)
        self._record("deleted", target, "deleted", None)

    def set_default(self, ref: str) -> Recording:
        """Make a recording the one ``present`` uses when no name is given."""
        return self.library.set_default(ref)

    # --- recording (US2) ----------------------------------------------------------------------

    def list_cameras(self) -> list[CameraInfo]:
        """Return the physical cameras, the default one (used by ``record``) first.

        Raises:
            DeviceError: If Windows cannot list the cameras.
        """
        if self._camera_lister is None:
            # Imported on use so presenting never loads camera code (FR-011).
            from teams_avatar.adapters.camera_list import CameraLister

            self._camera_lister = CameraLister()
        return self._camera_lister.list()

    def record(self, name: str | None, camera: str | None, ui: RecordingUI) -> RecordResult | None:
        """Record from a physical camera with a live preview; None if the user cancels.

        Args:
            name: Recording name, or None for ``Recording <date time>``.
            camera: Camera name as listed by ``list_cameras`` (case-insensitive), or None for
                the first one.
            ui: Preview window supplied by the front end.

        Raises:
            CameraUnavailableError: If no camera matches or it cannot be used.
            NameClashError: If ``name`` is already used.
            StorageFullError: If free space is below 200 MB.
        """
        cameras = self.list_cameras()
        chosen = self._pick_camera(cameras, camera)
        if self._camera_source is None:
            from teams_avatar.adapters.pyav_capture import CameraSource

            source: FrameSource = CameraSource()
        else:
            source = self._camera_source()
        session = RecordingSession(
            self.paths, self.library, self.activity, source, clock=self._clock
        )
        return session.run(name, chosen, ui)

    @staticmethod
    def _pick_camera(cameras: list[CameraInfo], wanted: str | None) -> CameraInfo:
        """Return the named camera, or the first one when ``wanted`` is None."""
        if not cameras:
            raise CameraUnavailableError(
                "any camera", "no camera was found", "Connect a camera, then try again."
            )
        if wanted is None:
            return cameras[0]
        for cam in cameras:
            if cam.name.casefold() == wanted.strip().casefold():
                return cam
        names = ", ".join(f'"{c.name}"' for c in cameras)
        raise CameraUnavailableError(
            wanted,
            "no camera has that name",
            f'Use one of {names} (see "teams-avatar cameras").',
        )

    def preview(self, ref: str, ui: PlaybackUI) -> None:
        """Play a recording once at 30 fps in ``ui``, ending with the loop blend.

        Never touches the camera service or the virtual camera. Esc or closing the window ends
        playback early.

        Raises:
            NotFoundError: If nothing matches ``ref``.
            RecordingUnreadableError: If the file is missing or cannot be decoded.
        """
        recording = self.library.find(ref)
        try:
            reader = pyav_codec.PyAVReader(self.paths.recording_file(recording.id))
        except UnreadableVideoError as exc:
            ui.close()
            raise RecordingUnreadableError(recording.name) from exc
        try:
            with reader:
                player = LoopPlayer(reader, reader.width, reader.height)
                due = self._clock()
                for frame in player.one_pass():
                    ui.show(frame)
                    if ui.poll_key() == "cancel" or ui.window_closed():
                        break
                    due += PREVIEW_FRAME_S
                    delay = due - self._clock()
                    if delay > 0:
                        self._sleep(delay)
        except UnreadableVideoError as exc:
            raise RecordingUnreadableError(recording.name) from exc
        finally:
            ui.close()

    def _presenting_id(self) -> str | None:
        """Return the id of the recording being presented, or None."""
        status = self.status()
        return status.recording_id if status.presenting else None

    def _refuse_if_presenting(self, recording: Recording, action: str) -> None:
        """Raise InUseError if ``recording`` is on the camera right now."""
        if self._presenting_id() == recording.id:
            raise InUseError(recording.name, action)

    def _record(self, event: str, rec: Recording, outcome: str, detail: str | None) -> None:
        """Append an activity record; a logging failure never undoes the user's change."""
        try:
            self.activity.append(
                ActivityRecord(
                    at=datetime.now(UTC),
                    event=event,
                    recording_id=rec.id,
                    recording_name=rec.name,
                    session_id=None,
                    outcome=outcome,
                    detail=detail,
                )
            )
        except (OSError, TimeoutError):
            log.warning("could not write the %s activity record", event, exc_info=True)

    def _resolve(self, ref: str | None) -> Recording:
        """Return the named recording, or the default one when ``ref`` is None."""
        if ref is not None:
            return self.library.find(ref)
        for recording in self.library.load():
            if recording.is_default:
                return recording
        raise NoDefaultError()

    def _service_reachable(self) -> bool:
        """Return whether a service process holds the pipe, even if it is busy or hung."""
        try:
            self.control.request({"op": "status"})
        except ServiceNotRunning:
            return False
        except ServiceError:
            return True
        return True

    def _idle_status(self) -> PresentationStatus:
        """Return the not-running status built from the saved settings."""
        settings = self.settings.load()
        return PresentationStatus(
            service_running=False,
            quality=settings.presentation_quality,
            indicator_on=settings.indicator_on,
        )

    @staticmethod
    def _parse_status(reply: dict[str, Any]) -> PresentationStatus:
        """Build a status from an ok reply, refusing replies the service should not send."""
        problem = "The camera service sent a reply this version does not understand"
        raw = reply.get("status")
        if not isinstance(raw, dict):
            raise ServiceError(problem)
        try:
            return PresentationStatus.from_dict(raw)
        except (TypeError, ValueError) as exc:
            raise ServiceError(problem) from exc

    def _status_via(self, message: dict[str, Any]) -> PresentationStatus:
        """Send ``message``; fall back to a settings-based status when not running."""
        try:
            reply = self.control.request(message)
        except ServiceNotRunning:
            return self._idle_status()
        if reply.get("ok") is not True:
            raise self._service_error(reply)
        return self._parse_status(reply)

    @staticmethod
    def _service_error(reply: dict[str, Any]) -> ServiceError:
        """Turn an error reply into a ``ServiceError`` that keeps the service's own message."""
        code = reply.get("error")
        if code == "shutting_down":
            return ServiceError(
                "The camera service is shutting down", "Try again in a few seconds."
            )
        if code in ("busy", "start_timeout"):
            # Slowness, not a broken file or service: retrying is the right advice.
            slow = str(reply.get("message") or "The camera service was too slow to answer")
            return ServiceError(slow.split(". ")[0], "Try again in a few seconds.")
        detail = reply.get("message")
        if isinstance(detail, str) and detail.strip():
            return ServiceError(detail.strip())
        return ServiceError("The camera service could not complete the request")

    def _start_service(self) -> None:
        """Start the service and wait for it to answer, within a real 10 s budget.

        Each poll is capped at the time left, so a silent service cannot stretch the wait. If
        the process exits first, the pipe is tried once more (a second instance that lost the
        single-instance race exits on purpose while the winner serves) before giving up.

        Raises:
            VirtualCameraError: If the service exited with the virtual-camera exit code.
            ServiceError: If it exits early or does not answer in time.
        """
        process = self.launcher.start()
        deadline = self._clock() + SERVICE_START_TIMEOUT_S
        while True:
            # Read the exit state before the poll so the final pipe try comes after the exit.
            exit_code = process.poll()
            left = deadline - self._clock()
            if left > 0:
                try:
                    self.control.request({"op": "status"}, timeout_s=left)
                    return
                except ServiceError:
                    # Not listening yet, or busy starting: both mean keep waiting.
                    pass
            if exit_code is not None:
                self._raise_died(exit_code)
            left = deadline - self._clock()
            if left <= 0:
                raise ServiceError("The camera service did not start within 10 seconds")
            self._sleep(min(SERVICE_POLL_INTERVAL_S, left))

    def _raise_died(self, exit_code: int) -> NoReturn:
        """Raise the error for a service process that exited before answering."""
        if exit_code == VirtualCameraError.exit_code:
            # The service names no reason on exit; the registry tells missing from busy.
            from teams_avatar.adapters.virtual_camera import obs_installed

            raise VirtualCameraError("in_use" if obs_installed() else "not_installed")
        raise ServiceError(
            f"The camera service stopped while starting (exit code {exit_code})",
            f"See the log at {self.paths.log_file} for the reason.",
        )

    def _present_error(self, reply: dict[str, Any], recording: Recording) -> TeamsAvatarError:
        """Map a service error reply to the matching facade error."""
        code = reply.get("error")
        if code == "already_presenting":
            return AlreadyPresentingError(self.status().recording_name)
        if code == "unreadable":
            return RecordingUnreadableError(recording.name)
        if code == "virtual_camera_not_installed":
            return VirtualCameraError("not_installed")
        if code == "virtual_camera_in_use":
            return VirtualCameraError("in_use")
        return self._service_error(reply)
