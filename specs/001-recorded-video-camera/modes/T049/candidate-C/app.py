"""Application facade: the only surface front ends use (FR-019, contracts/app-api.md).

It returns plain result objects and raises ``TeamsAvatarError`` subclasses; it never prints.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from teams_avatar import control as control_mod
from teams_avatar import importer
from teams_avatar.activity import ActivityLog
from teams_avatar.adapters import pyav_codec
from teams_avatar.errors import (
    AlreadyPresentingError,
    NoDefaultError,
    RecordingUnreadableError,
    ServiceError,
    ServiceNotRunning,
    UnreadableVideoError,
    VirtualCameraError,
)
from teams_avatar.launcher import ProcessLauncher, ServiceLauncher
from teams_avatar.library import Library
from teams_avatar.logging_setup import configure as configure_logging
from teams_avatar.models import (
    ActivityRecord,
    PresentationStatus,
    Quality,
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
class QualityResult:
    """Outcome of ``set_quality``."""

    settings: Settings
    applies_at_next_service_start: bool


class App:
    """Facade over the library, settings, activity log, recorder and camera service."""

    def __init__(
        self,
        paths: DataPaths | None = None,
        *,
        control: control_mod.ControlChannel | None = None,
        launcher: ServiceLauncher | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Wire the stores to a data folder and run startup reconciliation once per process.

        Args:
            paths: Data folder; defaults to ``DataPaths.resolve()``.
            control: Channel to the camera service; defaults to the per-user named pipe.
            launcher: Starts the service process; defaults to a real detached process.
            clock: Monotonic clock used for the service start timeout.
            sleep: Sleep function used while waiting for the service to start.
        """
        self.paths = (paths or DataPaths.resolve()).ensure()
        data_paths = self.paths
        self._control: control_mod.ControlChannel = control or control_mod.ControlClient(
            control_mod.pipe_name(), lambda: control_mod.read_key(data_paths)
        )
        self._launcher: ServiceLauncher = launcher or ProcessLauncher(self.paths)
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
        """Import a video file as a new recording (see ``importer.import_file``)."""
        return importer.import_file(self.paths, self.library, self.activity, path, name)

    def _running_status(self) -> PresentationStatus | None:
        """Return the service status, or ``None`` when the service is not running."""
        try:
            reply = self._control.request({"op": "status"})
        except ServiceNotRunning:
            return None
        return self._status_from(reply)

    @staticmethod
    def _status_from(reply: dict[str, Any]) -> PresentationStatus:
        """Build a status from a reply, raising ``ServiceError`` for an error reply."""
        status = reply.get("status")
        if not reply.get("ok", False) or not isinstance(status, dict):
            raise ServiceError(str(reply.get("message") or "The camera service gave a bad reply"))
        return PresentationStatus.from_dict(status)

    def _stopped_status(self) -> PresentationStatus:
        """Status shown while the service is not running, taken from saved settings."""
        saved = self.settings.load()
        return PresentationStatus(
            service_running=False,
            quality=saved.presentation_quality,
            indicator_on=saved.indicator_on,
        )

    def _start_service(self) -> None:
        """Start the service and wait up to 10 s for it to answer status requests.

        Raises:
            ServiceError: If the service does not answer within the timeout.
        """
        self._launcher.start()
        deadline = self._clock() + SERVICE_START_TIMEOUT_S
        while True:
            try:
                self._control.request({"op": "status"})
                return
            except ServiceNotRunning:
                pass
            if self._clock() >= deadline:
                raise ServiceError(
                    "The camera service did not start within 10 seconds",
                    'Try again, or run "teams-avatar shutdown" first.',
                )
            self._sleep(SERVICE_POLL_INTERVAL_S)

    def present(self, ref: str | None) -> PresentationStatus:
        """Present a recording (or the default one) through the virtual camera.

        Starts the service when needed. Presenting the current recording again succeeds.

        Raises:
            NoDefaultError: If ``ref`` is ``None`` and no default is set.
            NotFoundError: If no recording matches ``ref``.
            RecordingUnreadableError: If the video file is missing or damaged.
            AlreadyPresentingError: If a different recording is being presented.
            VirtualCameraError: If OBS Virtual Camera is missing or in use.
            ServiceError: If the service cannot be started or fails.
        """
        recordings = self.library.load()
        if ref is None:
            default = next((r for r in recordings if r.is_default), None)
            if default is None:
                raise NoDefaultError()
            rec = default
        else:
            rec = self.library.find(ref)
        path = self.paths.recording_file(rec.id)
        # Checked here so a bad file never starts the service or touches the virtual camera.
        try:
            pyav_codec.probe(path)
        except (UnreadableVideoError, OSError) as exc:
            raise RecordingUnreadableError(rec.name) from exc
        saved = self.settings.load()
        message: dict[str, Any] = {
            "op": "present",
            "recording_id": rec.id,
            "name": rec.name,
            "path": str(path),
            "quality": saved.presentation_quality,
            "indicator": saved.indicator_on,
        }
        try:
            reply = self._control.request(message)
        except ServiceNotRunning:
            self._start_service()
            reply = self._control.request(message)
        if not reply.get("ok", False):
            self._raise_for(str(reply.get("error")), str(reply.get("message", "")), rec.name)
        return replace(
            self._status_from(reply),
            recording_width=rec.width,
            recording_height=rec.height,
            warnings=list(rec.warnings),
        )

    def _raise_for(self, code: str, message: str, name: str) -> None:
        """Translate a service error reply into the matching ``TeamsAvatarError``."""
        if code == "already_presenting":
            current = self._running_status()
            raise AlreadyPresentingError(
                (current.recording_name if current else None) or "Another recording"
            )
        if code == "unreadable":
            raise RecordingUnreadableError(name)
        if code == "virtual_camera_not_installed":
            raise VirtualCameraError("not_installed")
        if code == "virtual_camera_in_use":
            raise VirtualCameraError("in_use")
        raise ServiceError(message or "The camera service reported a problem")

    def stop(self) -> PresentationStatus:
        """Stop presenting; a no-op status when the service is not running."""
        try:
            reply = self._control.request({"op": "stop"})
        except ServiceNotRunning:
            return self._stopped_status()
        return self._status_from(reply)

    def status(self) -> PresentationStatus:
        """Return what the service is doing, or a not-running status built from settings."""
        return self._running_status() or self._stopped_status()

    def set_indicator(self, on: bool) -> PresentationStatus:
        """Save the indicator setting and apply it to a running service."""
        self.settings.update(indicator_on=on)
        try:
            reply = self._control.request({"op": "indicator", "on": on})
        except ServiceNotRunning:
            return self._stopped_status()
        return self._status_from(reply)

    def set_quality(self, quality: Quality) -> QualityResult:
        """Save the presentation quality; a running service applies it at its next start."""
        saved = self.settings.update(presentation_quality=quality)
        return QualityResult(saved, self._running_status() is not None)

    def shutdown(self) -> None:
        """Ask the service to exit; a no-op when it is not running."""
        try:
            self._control.request({"op": "shutdown"})
        except ServiceNotRunning:
            return
