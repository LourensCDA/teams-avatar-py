"""Application facade: the only surface front ends use (FR-019, contracts/app-api.md).

It returns plain result objects and raises ``TeamsAvatarError`` subclasses; it never prints.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from teams_avatar import control as control_module
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
    """Outcome of changing the presentation quality."""

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
            control: Channel to the camera service; defaults to the named-pipe client.
            launcher: Starts the service process; defaults to a detached subprocess.
            clock: Monotonic clock used for the service start timeout.
            sleep: Sleep function used while waiting for the service to start.
        """
        self.paths = (paths or DataPaths.resolve()).ensure()
        data = self.paths
        self.control: ControlChannel = control or control_module.ControlClient(
            control_module.pipe_name(), lambda: control_module.read_key(data)
        )
        self.launcher: ServiceLauncher = launcher or SubprocessLauncher(str(data.root))
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
        """Import a video file as a new recording."""
        return importer.import_file(self.paths, self.library, self.activity, path, name)

    def _try(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Send a request; return ``None`` when the service is not running."""
        try:
            return self.control.request(message)
        except ServiceNotRunning:
            return None

    def _stored_status(self) -> PresentationStatus:
        """Status for a stopped service, built from saved settings."""
        saved = self.settings.load()
        return PresentationStatus(
            service_running=False,
            quality=saved.presentation_quality,
            indicator_on=saved.indicator_on,
        )

    @staticmethod
    def _status_of(reply: dict[str, Any]) -> PresentationStatus:
        """Build a status from a successful control reply."""
        return PresentationStatus.from_dict(reply.get("status", {"service_running": True}))

    def _wait_for_service(self) -> None:
        """Start the service and poll until it answers.

        Raises:
            ServiceError: If it does not answer within 10 seconds.
        """
        self.launcher.start()
        deadline = self._clock() + SERVICE_START_TIMEOUT_S
        while self._try({"op": "status"}) is None:
            if self._clock() >= deadline:
                raise ServiceError("The camera service did not start within 10 seconds")
            self._sleep(SERVICE_POLL_INTERVAL_S)

    def _raise_for(self, reply: dict[str, Any], name: str) -> None:
        """Turn an error reply from ``present`` into the matching exception."""
        code = reply.get("error")
        if code == "already_presenting":
            current = self._try({"op": "status"})
            running = (current or {}).get("status", {}).get("recording_name")
            raise AlreadyPresentingError(running or "another recording")
        if code == "unreadable":
            raise RecordingUnreadableError(name)
        if code == "virtual_camera_not_installed":
            raise VirtualCameraError("not_installed")
        if code == "virtual_camera_in_use":
            raise VirtualCameraError("in_use")
        raise ServiceError(str(reply.get("message") or "The camera service reported an error"))

    def present(self, ref: str | None) -> PresentationStatus:
        """Present a recording (the default one when ``ref`` is ``None``) as the camera.

        Raises:
            NoDefaultError: If ``ref`` is ``None`` and no default is set.
            RecordingUnreadableError: If the video file is missing or undecodable.
            AlreadyPresentingError: If a different recording is being presented.
            VirtualCameraError: If OBS Virtual Camera is missing or in use.
            ServiceError: If the service cannot be started or fails.
        """
        if ref is None:
            default = next((r for r in self.library.load() if r.is_default), None)
            if default is None:
                raise NoDefaultError()
            recording = default
        else:
            recording = self.library.find(ref)
        file = self.paths.recording_file(recording.id)
        try:
            pyav_codec.probe(file)
        except (UnreadableVideoError, OSError) as exc:
            raise RecordingUnreadableError(recording.name) from exc
        saved = self.settings.load()
        message: dict[str, Any] = {
            "op": "present",
            "recording_id": recording.id,
            "name": recording.name,
            "path": str(file),
            "quality": saved.presentation_quality,
            "indicator": saved.indicator_on,
        }
        reply = self._try(message)
        if reply is None:
            self._wait_for_service()
            reply = self._try(message)
            if reply is None:
                raise ServiceNotRunning()
        if not reply.get("ok"):
            self._raise_for(reply, recording.name)
        status = self._status_of(reply).to_dict()
        status.update(
            recording_width=recording.width,
            recording_height=recording.height,
            warnings=list(recording.warnings),
        )
        return PresentationStatus.from_dict(status)

    def status(self) -> PresentationStatus:
        """Return the service status, or one built from settings when it is not running."""
        reply = self._try({"op": "status"})
        if reply is None or not reply.get("ok"):
            return self._stored_status()
        return self._status_of(reply)

    def stop(self) -> PresentationStatus:
        """Stop presenting; a no-op when the service is not running."""
        reply = self._try({"op": "stop"})
        if reply is None:
            return self._stored_status()
        if not reply.get("ok"):
            raise ServiceError(str(reply.get("message") or "The camera service reported an error"))
        return self._status_of(reply)

    def set_indicator(self, on: bool) -> PresentationStatus:
        """Save the indicator setting and forward it to a running service."""
        self.settings.update(indicator_on=on)
        reply = self._try({"op": "indicator", "on": on})
        if reply is None or not reply.get("ok"):
            return self._stored_status()
        return self._status_of(reply)

    def set_quality(self, quality: Quality) -> QualityResult:
        """Save the quality; a running service only picks it up when it next starts."""
        saved = self.settings.update(presentation_quality=quality)
        running = self._try({"op": "status"}) is not None
        return QualityResult(saved, running)

    def shutdown(self) -> None:
        """Ask the service to exit; a no-op when it is not running."""
        self._try({"op": "shutdown"})
