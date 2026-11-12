"""Application facade: the only surface front ends use (FR-019, contracts/app-api.md).

It returns plain result objects and raises ``TeamsAvatarError`` subclasses; it never prints.
"""

from __future__ import annotations

import dataclasses
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from teams_avatar import control as control_mod
from teams_avatar import importer
from teams_avatar.activity import ActivityLog
from teams_avatar.adapters import pyav_codec
from teams_avatar.control import ControlChannel
from teams_avatar.errors import (
    AlreadyPresentingError,
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
from teams_avatar.logging_setup import configure as configure_logging
from teams_avatar.models import (
    ActivityRecord,
    PresentationStatus,
    Quality,
    Recording,
    RecordResult,
    Settings,
)
from teams_avatar.paths import DataPaths
from teams_avatar.reconcile import ReconcileReport, reconcile
from teams_avatar.settings import SettingsStore

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


SERVICE_START_TIMEOUT_S = 10.0
SERVICE_POLL_INTERVAL_S = 0.1


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
    ) -> None:
        """Wire the stores to a data folder and run startup reconciliation once per process.

        Args:
            paths: Data folder; defaults to ``DataPaths.resolve()``.
            control: Channel to the camera service; defaults to the per-user named pipe.
            launcher: Starts the service when it is not running; defaults to a real process.
            clock: Monotonic clock used for the service start timeout.
            sleep: Sleep function used between start-up polls.
        """
        self.paths = (paths or DataPaths.resolve()).ensure()
        data = self.paths
        self.control: ControlChannel = control or control_mod.ControlClient(
            control_mod.pipe_name(), lambda: control_mod.read_key(data)
        )
        self.launcher: ServiceLauncher = launcher or SubprocessLauncher(self.paths)
        self._clock = clock
        self._sleep = sleep
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
            reply = self.control.request(message)
        except ServiceNotRunning:
            self._start_service()
            reply = self.control.request(message)
        if not reply.get("ok"):
            raise self._present_error(str(reply.get("error")), recording)
        status = PresentationStatus.from_dict(reply["status"])
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
        """Return the service status, or a not-running status built from the settings."""
        return self._status_via({"op": "status"})

    def set_indicator(self, on: bool) -> PresentationStatus:
        """Save the indicator setting and apply it live if the service is running."""
        self.settings.update(indicator_on=on)
        return self._status_via({"op": "indicator", "on": on})

    def set_quality(self, quality: Quality) -> QualityChange:
        """Save the presentation quality; it only affects the next service start."""
        saved = self.settings.update(presentation_quality=quality)
        return QualityChange(saved, self._service_running())

    def shutdown(self) -> None:
        """Ask the service to exit; a no-op when it is not running."""
        try:
            self.control.request({"op": "shutdown"})
        except ServiceNotRunning:
            return

    def _resolve(self, ref: str | None) -> Recording:
        """Return the named recording, or the default one when ``ref`` is None."""
        if ref is not None:
            return self.library.find(ref)
        for recording in self.library.load():
            if recording.is_default:
                return recording
        raise NoDefaultError()

    def _service_running(self) -> bool:
        """Return whether a service answers a status request."""
        try:
            self.control.request({"op": "status"})
        except ServiceNotRunning:
            return False
        return True

    def _status_via(self, message: dict[str, Any]) -> PresentationStatus:
        """Send ``message``; fall back to a settings-based status when not running."""
        try:
            reply = self.control.request(message)
        except ServiceNotRunning:
            settings = self.settings.load()
            return PresentationStatus(
                service_running=False,
                quality=settings.presentation_quality,
                indicator_on=settings.indicator_on,
            )
        if not reply.get("ok"):
            raise ServiceError(
                str(reply.get("message") or "The camera service refused the request")
            )
        return PresentationStatus.from_dict(reply["status"])

    def _start_service(self) -> None:
        """Start the service and wait up to 10 s for it to answer.

        Raises:
            ServiceError: If it does not answer in time.
        """
        self.launcher.start()
        deadline = self._clock() + SERVICE_START_TIMEOUT_S
        while True:
            try:
                self.control.request({"op": "status"})
                return
            except ServiceError:
                # Not listening yet, or busy starting: both mean keep waiting.
                if self._clock() >= deadline:
                    raise ServiceError(
                        "The camera service did not start within 10 seconds"
                    ) from None
                self._sleep(SERVICE_POLL_INTERVAL_S)

    def _present_error(self, code: str, recording: Recording) -> TeamsAvatarError:
        """Map a service error code to the matching facade error."""
        if code == "already_presenting":
            try:
                current = self.status().recording_name
            except TeamsAvatarError:
                current = None
            return AlreadyPresentingError(current or "another recording")
        if code == "unreadable":
            return RecordingUnreadableError(recording.name)
        if code == "virtual_camera_not_installed":
            return VirtualCameraError("not_installed")
        if code == "virtual_camera_in_use":
            return VirtualCameraError("in_use")
        return ServiceError("The camera service could not present the recording")
