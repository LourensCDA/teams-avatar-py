import re

import pytest

from teams_avatar import errors as e

# (error instance, expected exit code) per contracts/cli.md
CASES: list[tuple[e.TeamsAvatarError, int]] = [
    (e.CameraUnavailableError("Integrated Camera", "it is in use by another program"), 1),
    (e.NameClashError("Office"), 3),
    (e.StorageFullError(), 1),
    (e.ImportRejectedError("clip.mp4", "it is not a video file"), 1),
    (e.NotFoundError("Office"), 1),
    (e.InUseError("Office", "delete"), 3),
    (e.NoDefaultError(), 1),
    (e.AlreadyPresentingError("Office"), 3),
    (e.RecordingUnreadableError("Office"), 1),
    (e.UnreadableVideoError("the file is truncated"), 1),
    (e.VirtualCameraError("not_installed"), 4),
    (e.VirtualCameraError("in_use"), 4),
    (e.ServiceError("The camera service did not answer"), 1),
    (e.ServiceNotRunning(), 1),
    (e.DeviceError("The camera list could not be read"), 1),
    (e.NoticeNotAcknowledgedError("notice text"), 3),
    (e.InvalidArgumentError("The name is empty", "Give a name of 1-64 characters."), 2),
    (e.DataFileError("library.json", "it is not valid JSON"), 1),
    (e.ConfirmationRequiredError("Office"), 3),
    (e.LibraryWriteError("The library could not be written"), 1),
]

MESSAGE = re.compile(r"^[A-Z\"].*[^.]\. [A-Z\"].+$", re.DOTALL)


@pytest.mark.parametrize(("error", "code"), CASES, ids=lambda v: type(v).__name__)
def test_message_and_exit_code(error: e.TeamsAvatarError, code: int) -> None:
    assert isinstance(error, e.TeamsAvatarError)
    assert error.exit_code == code
    assert MESSAGE.match(error.message), error.message
    assert error.message == f"{error.cause}. {error.next_step}"
    assert str(error) == error.message


def test_virtual_camera_kinds_have_distinct_messages() -> None:
    missing = e.VirtualCameraError("not_installed")
    busy = e.VirtualCameraError("in_use")
    assert missing.kind == "not_installed"
    assert busy.kind == "in_use"
    assert "OBS" in missing.message
    assert missing.message != busy.message


def test_in_use_matches_contract_example() -> None:
    err = e.InUseError("Office blue shirt", "delete")
    assert err.message == (
        '"Office blue shirt" is being presented. Run "teams-avatar stop" first, then delete it.'
    )


def test_service_not_running_is_a_service_error() -> None:
    assert isinstance(e.ServiceNotRunning(), e.ServiceError)
