"""Contract tests for the OBS Virtual Camera sink.

Live tests need OBS Studio and run only with TEAMS_AVATAR_HW=1; the error mapping is checked
without hardware by feeding the adapter pyvirtualcam's probed messages (research R1).
"""

from __future__ import annotations

import numpy as np
import pytest

from teams_avatar.adapters import virtual_camera
from teams_avatar.adapters.virtual_camera import ObsFrameSink, map_backend_error
from teams_avatar.errors import VirtualCameraError


def test_message_mapping_without_hardware() -> None:
    missing = map_backend_error(
        RuntimeError("'obs' backend: OBS Virtual Camera device not found! Did you install OBS?")
    )
    busy = map_backend_error(
        RuntimeError("'obs' backend: virtual camera output could not be started")
    )
    assert isinstance(missing, VirtualCameraError) and missing.kind == "not_installed"
    assert isinstance(busy, VirtualCameraError) and busy.kind == "in_use"
    assert map_backend_error(RuntimeError("something else")) is None


@pytest.mark.hardware
def test_send_90_frames_at_720p() -> None:
    sink = ObsFrameSink()
    sink.open(1280, 720, 30)
    try:
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        for i in range(90):
            frame[:] = i
            sink.send(frame)
            sink.sleep_until_next_frame()
    finally:
        sink.close()


@pytest.mark.hardware
def test_second_sender_is_in_use() -> None:
    first = ObsFrameSink()
    first.open(1280, 720, 30)
    try:
        with pytest.raises(VirtualCameraError) as info:
            ObsFrameSink().open(1280, 720, 30)
        assert info.value.kind == "in_use"
    finally:
        first.close()


@pytest.mark.hardware
def test_missing_obs_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(virtual_camera, "obs_installed", lambda: False)
    with pytest.raises(VirtualCameraError) as info:
        ObsFrameSink().open(1280, 720, 30)
    assert info.value.kind == "not_installed"
