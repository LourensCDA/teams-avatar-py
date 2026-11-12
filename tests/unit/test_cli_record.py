"""US2 facade and CLI: cameras and record (contracts/app-api.md, contracts/cli.md)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from teams_avatar import cli
from teams_avatar.app import App
from teams_avatar.errors import CameraUnavailableError, DeviceError
from teams_avatar.models import CameraInfo
from teams_avatar.paths import DataPaths
from tests.fakes import FakeCameraSource, FakeRecordingUI

CAMS = [
    CameraInfo(name="Integrated Camera", device_name="Integrated Camera"),
    CameraInfo(name="USB Cam", device_name="USB Cam"),
]


class Lister:
    def __init__(self, cams: list[CameraInfo] | None = None, fail: bool = False) -> None:
        self.cams = CAMS if cams is None else cams
        self.fail = fail

    def list(self) -> list[CameraInfo]:
        if self.fail:
            raise DeviceError("The camera list could not be read")
        return list(self.cams)


class Env:
    def __init__(self, data_home: Path, lister: Lister, source: FakeCameraSource) -> None:
        self.paths = DataPaths.resolve().ensure()
        self.lister = lister
        self.source = source

    def app(self) -> App:
        return App(self.paths, camera_lister=self.lister, camera_source=lambda: self.source)

    def run(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(
            [*argv, "--accept-notice"],
            stdin=io.StringIO(""),
            stdout=out,
            stderr=err,
            app_factory=self.app,
        )
        return code, out.getvalue(), err.getvalue()


@pytest.fixture
def env(data_home: Path) -> Env:
    return Env(data_home, Lister(), FakeCameraSource())


def test_list_cameras(env: Env) -> None:
    assert env.app().list_cameras() == CAMS


def test_record_default_camera_and_name(env: Env) -> None:
    result = env.app().record(None, None, FakeRecordingUI("save", after=60))
    assert result is not None
    assert env.source.opened == ["Integrated Camera"]
    assert result.recording.name.startswith("Recording ")


def test_record_named_camera(env: Env) -> None:
    env.app().record("Desk", "usb cam", FakeRecordingUI("save", after=60))
    assert env.source.opened == ["USB Cam"]


def test_record_unknown_camera(env: Env) -> None:
    with pytest.raises(CameraUnavailableError, match="Nope"):
        env.app().record(None, "Nope", FakeRecordingUI())


def test_record_without_cameras(data_home: Path) -> None:
    env = Env(data_home, Lister([]), FakeCameraSource())
    with pytest.raises(CameraUnavailableError):
        env.app().record(None, None, FakeRecordingUI())


def test_record_cancel_returns_none(env: Env) -> None:
    assert env.app().record(None, None, FakeRecordingUI("cancel", after=5)) is None


def test_cli_cameras(env: Env) -> None:
    code, out, _ = env.run("cameras")
    assert code == 0
    assert out.splitlines() == ["Integrated Camera (default)", "USB Cam"]
    code, out, _ = env.run("cameras", "--json")
    assert [c["name"] for c in json.loads(out)["cameras"]] == ["Integrated Camera", "USB Cam"]


def test_cli_cameras_none(data_home: Path) -> None:
    env = Env(data_home, Lister([]), FakeCameraSource())
    code, out, _ = env.run("cameras")
    assert code == 0 and "No camera" in out


def test_cli_cameras_failure_exit_1(data_home: Path) -> None:
    env = Env(data_home, Lister(fail=True), FakeCameraSource())
    code, _, err = env.run("cameras")
    assert code == 1 and err.startswith("error: The camera list could not be read.")


def test_cli_record_saved(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "make_recording_ui", lambda: FakeRecordingUI("save", after=180))
    code, out, _ = env.run("record", "--name", "Office", "--camera", "USB Cam")
    assert code == 0
    assert out.startswith('Saved "Office": 0:00:06, 320x180')


def test_cli_record_cancelled(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "make_recording_ui", lambda: FakeRecordingUI("cancel", after=5))
    code, out, _ = env.run("record")
    assert code == 0 and "Cancelled" in out and "nothing was saved" in out


def test_cli_record_camera_busy_exit_1(data_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    err_ = CameraUnavailableError("Integrated Camera", "it is in use by another program")
    env = Env(data_home, Lister(), FakeCameraSource(open_error=err_))
    monkeypatch.setattr(cli, "make_recording_ui", lambda: FakeRecordingUI())
    code, _, err = env.run("record")
    assert code == 1 and "in use" in err
