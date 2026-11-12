"""Camera listing from captured PowerShell output (no devices needed)."""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from teams_avatar.adapters.camera_list import CameraLister
from teams_avatar.errors import DeviceError
from teams_avatar.models import CameraInfo


def runner(stdout: str, code: int = 0) -> Any:  # noqa: ANN401  # fake runner with a .calls list
    calls: list[list[str]] = []

    def run(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        assert timeout == 10
        return subprocess.CompletedProcess(args, code, stdout=stdout, stderr="boom")

    run.calls = calls  # type: ignore[attr-defined]
    return run


def rows(*items: tuple[str, str]) -> str:
    return json.dumps([{"Name": n, "PNPClass": c, "Status": "OK"} for n, c in items])


def test_lists_camera_and_image_classes_without_obs() -> None:
    out = rows(
        ("Integrated Camera", "Camera"),
        ("OBS Virtual Camera", "Camera"),
        ("USB Webcam", "Image"),
    )
    cams = CameraLister(run=runner(out)).list()
    assert [c.name for c in cams] == ["Integrated Camera", "USB Webcam"]
    assert cams[0] == CameraInfo(name="Integrated Camera", device_name="Integrated Camera")


def test_single_object_json() -> None:
    out = json.dumps({"Name": "Integrated Camera", "PNPClass": "Camera", "Status": "OK"})
    assert [c.name for c in CameraLister(run=runner(out)).list()] == ["Integrated Camera"]


def test_empty_output_is_no_cameras() -> None:
    assert CameraLister(run=runner("")).list() == []


def test_duplicates_numbered() -> None:
    out = rows(("HD Webcam", "Camera"), ("HD Webcam", "Camera"), ("HD Webcam", "Camera"))
    cams = CameraLister(run=runner(out)).list()
    assert [c.name for c in cams] == ["HD Webcam", "HD Webcam #2", "HD Webcam #3"]
    assert [c.device_number for c in cams] == [0, 1, 2]
    assert all(c.device_name == "HD Webcam" for c in cams)


def test_uses_cim_query_through_powershell() -> None:
    run = runner(rows(("Integrated Camera", "Camera")))
    CameraLister(run=run).list()
    cmd = " ".join(run.calls[0])
    assert "powershell" in cmd.lower()
    assert "Win32_PnPEntity" in cmd


@pytest.mark.parametrize(("stdout", "code"), [("", 1), ("{not json", 0), ("[1, 2]", 0)])
def test_failures_raise_device_error(stdout: str, code: int) -> None:
    with pytest.raises(DeviceError):
        CameraLister(run=runner(stdout, code)).list()


def test_timeout_raises_device_error() -> None:
    def slow(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(args, timeout)

    with pytest.raises(DeviceError):
        CameraLister(run=slow).list()
