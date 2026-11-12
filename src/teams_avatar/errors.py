"""Errors with plain-language messages "<cause>. <what to do next>" and CLI exit codes (FR-014).

Exit codes follow contracts/cli.md: 1 failed, 2 invalid usage, 3 refused by a rule,
4 virtual camera unavailable.
"""

from __future__ import annotations

from typing import ClassVar, Literal

VirtualCameraKind = Literal["not_installed", "in_use"]


class TeamsAvatarError(Exception):
    """Base error; ``message`` is shown to the user as ``error: <message>``."""

    exit_code: ClassVar[int] = 1

    def __init__(self, cause: str, next_step: str) -> None:
        """Store the cause (without trailing full stop) and the suggested next step."""
        self.cause = cause.rstrip(". ")
        self.next_step = next_step
        super().__init__(self.message)

    @property
    def message(self) -> str:
        """Return the full user-facing message."""
        return f"{self.cause}. {self.next_step}"


class InvalidArgumentError(TeamsAvatarError):
    """A command argument is not acceptable (e.g. a recording name breaks the name rules)."""

    exit_code = 2


class DataFileError(TeamsAvatarError):
    """A data file is unreadable or has an unexpected format; it is refused, not partly read."""

    def __init__(self, file_name: str, reason: str) -> None:
        """Describe which data file is bad and why."""
        super().__init__(
            f"The data file {file_name} cannot be used: {reason}",
            "Restore it from a backup or move it aside; the tool will not change it.",
        )


class CameraUnavailableError(TeamsAvatarError):
    """The physical camera cannot be opened or used for recording."""

    def __init__(self, camera: str, reason: str, next_step: str | None = None) -> None:
        """Name the camera and the reason; ``next_step`` overrides the generic advice."""
        super().__init__(
            f'The camera "{camera}" cannot be used: {reason}',
            next_step
            or 'Close other programs using it, or pick another with "teams-avatar cameras".',
        )


class NameClashError(TeamsAvatarError):
    """A recording with that name already exists; nothing is replaced."""

    exit_code = 3

    def __init__(self, name: str) -> None:
        """Name the clashing recording."""
        super().__init__(f'A recording named "{name}" already exists', "Choose another name.")


class StorageFullError(TeamsAvatarError):
    """Too little free disk space to record or import."""

    def __init__(self) -> None:
        """Build the standard storage-full message."""
        super().__init__(
            "There is not enough free disk space (at least 200 MB is needed)",
            "Free some space or delete recordings you no longer need, then try again.",
        )


class ImportRejectedError(TeamsAvatarError):
    """An import was refused; the library is unchanged."""

    def __init__(self, file_name: str, reason: str) -> None:
        """Name the rejected file and the reason."""
        self.reason = reason
        super().__init__(
            f'"{file_name}" was not imported: {reason}',
            "Choose a playable video file (for example MP4) and try again.",
        )


class LibraryWriteError(TeamsAvatarError):
    """A recording could not be written to the data folder (disk or permission problem)."""

    def __init__(self, reason: str) -> None:
        """Describe why the recording could not be stored."""
        self.reason = reason
        super().__init__(
            f"The recording could not be saved: {reason}",
            "Check that the data folder is writable and has free space, then try again.",
        )


class DuplicateIdError(LibraryWriteError):
    """A library entry with this recording id already exists; nothing is appended."""

    def __init__(self, recording_id: str) -> None:
        """Name the id that is already registered."""
        super().__init__(f"a recording with id {recording_id} is already in the library")


class NotFoundError(TeamsAvatarError):
    """No recording matches the given name or id prefix."""

    def __init__(self, ref: str) -> None:
        """Name the reference that did not match."""
        super().__init__(
            f'No recording matches "{ref}"',
            'Run "teams-avatar list" to see names, or give at least 6 characters of its id.',
        )


class InUseError(TeamsAvatarError):
    """The recording is being presented, so it cannot be changed."""

    exit_code = 3

    def __init__(self, name: str, action: str) -> None:
        """Name the recording and the refused action (e.g. ``delete``)."""
        super().__init__(
            f'"{name}" is being presented',
            f'Run "teams-avatar stop" first, then {action} it.',
        )


class NoDefaultError(TeamsAvatarError):
    """``present`` was run without a name and no default recording is set."""

    def __init__(self) -> None:
        """Build the standard no-default message."""
        super().__init__(
            "No recording name was given and no default recording is set",
            'Name a recording, or set one with "teams-avatar default <recording>".',
        )


class AlreadyPresentingError(TeamsAvatarError):
    """A different recording is already being presented (FR-010)."""

    exit_code = 3

    def __init__(self, current_name: str | None) -> None:
        """Name the recording currently presenting, or say "another" when it is unknown."""
        self.current_name = current_name
        subject = f'"{current_name}"' if current_name else "Another recording"
        super().__init__(
            f"{subject} is already being presented",
            'Run "teams-avatar stop" first, then present the other recording.',
        )


class RecordingUnreadableError(TeamsAvatarError):
    """A library recording's video file is missing or cannot be decoded."""

    def __init__(self, name: str) -> None:
        """Name the unreadable recording."""
        super().__init__(
            f'The video file of "{name}" is missing or cannot be read',
            'Delete it with "teams-avatar delete" and record or import it again.',
        )


class UnreadableVideoError(TeamsAvatarError):
    """A video file cannot be opened or decoded (raised by the codec adapter)."""

    def __init__(self, reason: str) -> None:
        """Describe why the video cannot be read."""
        self.reason = reason
        super().__init__(f"The video cannot be read: {reason}", "Use a different video file.")


class VirtualCameraError(TeamsAvatarError):
    """OBS Virtual Camera is missing or already driven by another program."""

    exit_code = 4

    def __init__(self, kind: VirtualCameraKind) -> None:
        """Build the message for ``kind`` (``not_installed`` or ``in_use``)."""
        self.kind: VirtualCameraKind = kind
        if kind == "not_installed":
            super().__init__(
                "OBS Virtual Camera is not installed",
                "Install OBS Studio from obsproject.com, then run the command again.",
            )
        else:
            super().__init__(
                "OBS Virtual Camera is already in use by another program",
                'Stop "Start Virtual Camera" in OBS (or the other program), then try again.',
            )


class ServiceError(TeamsAvatarError):
    """The camera service failed or did not answer."""

    def __init__(self, detail: str, next_step: str | None = None) -> None:
        """Describe the service problem."""
        super().__init__(
            detail,
            next_step or 'Run "teams-avatar shutdown", then try again.',
        )


class ServiceNotRunning(ServiceError):
    """No camera service is listening on the control pipe."""

    def __init__(self) -> None:
        """Build the standard not-running message."""
        super().__init__(
            "The camera service is not running",
            'Run "teams-avatar present" to start it.',
        )


class DeviceError(TeamsAvatarError):
    """A Windows device query failed."""

    def __init__(self, detail: str) -> None:
        """Describe the failed device query."""
        super().__init__(detail, "Check that the camera is connected and enabled, then try again.")


class NoticeNotAcknowledgedError(TeamsAvatarError):
    """The first-use notice has not been accepted yet."""

    exit_code = 3

    def __init__(self, notice: str) -> None:
        """Carry the notice text so the CLI can show it."""
        self.notice = notice
        super().__init__(
            "The first-use notice has not been accepted",
            "Run the command in a terminal and type y, or add --accept-notice.",
        )


class ConfirmationRequiredError(TeamsAvatarError):
    """A destructive command was not confirmed (constitution Principle IV)."""

    exit_code = 3

    def __init__(self, name: str, declined: bool = False) -> None:
        """Name the recording whose deletion was not confirmed."""
        cause = (
            f'Deleting "{name}" was cancelled'
            if declined
            else f'Deleting "{name}" needs confirmation'
        )
        super().__init__(cause, "Run the command in a terminal and type y, or add --yes.")
