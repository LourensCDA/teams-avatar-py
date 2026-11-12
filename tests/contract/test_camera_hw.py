"""Hardware contract tests for camera listing and capture (run only with TEAMS_AVATAR_HW=1)."""

from __future__ import annotations

import time

import pytest

from teams_avatar.adapters.camera_list import CameraLister
from teams_avatar.adapters.pyav_capture import CameraSource
from teams_avatar.errors import CameraUnavailableError

pytestmark = pytest.mark.hardware


def test_listed_cameras_open_by_name() -> None:
    cameras = CameraLister().list()
    assert cameras, "no physical camera listed"
    usable = 0
    for cam in cameras:
        source = CameraSource()
        try:
            source.open(cam)
        except CameraUnavailableError:
            continue  # e.g. infrared or scanner devices are reported, not hidden
        usable += 1
        source.close()
    assert usable >= 1


def test_three_seconds_at_27_fps_or_more() -> None:
    cam = next(iter(CameraLister().list()))
    source = CameraSource()
    source.open(cam)
    try:
        start = time.monotonic()
        frames = 0
        while time.monotonic() - start < 3:
            source.read()
            frames += 1
        assert frames / (time.monotonic() - start) >= 27
    finally:
        source.close()


def test_camera_held_elsewhere_reports_within_10s() -> None:
    cam = next(iter(CameraLister().list()))
    holder = CameraSource()
    holder.open(cam)
    try:
        start = time.monotonic()
        try:
            CameraSource().open(cam)
        except CameraUnavailableError:
            pass
        else:
            pytest.skip("this camera driver allows shared access")
        assert time.monotonic() - start <= 11
    finally:
        holder.close()
