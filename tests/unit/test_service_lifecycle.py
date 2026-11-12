"""run_service start-up rules: single instance and an unavailable virtual camera."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

import teams_avatar.service as service_module
from teams_avatar.control import pipe_name
from teams_avatar.errors import VirtualCameraError
from teams_avatar.locks import ServiceLock
from teams_avatar.paths import DataPaths
from teams_avatar.service import run_service
from tests.fakes import FakeFrameSink


def test_losing_instance_returns_at_once_and_keeps_the_winners_key(data_home: Path) -> None:
    paths = DataPaths.resolve().ensure()
    paths.service_key.write_text("aa" * 32, encoding="ascii")
    winner = ServiceLock(paths.service_lock)
    assert winner.try_acquire()
    try:
        sink = FakeFrameSink()
        assert run_service(paths, sink, pipe_name(suffix=f"t-{uuid.uuid4().hex}")) == 0
        assert paths.service_key.read_text(encoding="ascii") == "aa" * 32
        assert sink.size is None  # never touched the camera
    finally:
        winner.release()


class _BrokenSink(FakeFrameSink):
    def __init__(self, kind: str) -> None:
        super().__init__()
        self.kind = kind

    def open(self, width: int, height: int, fps: float) -> None:
        raise VirtualCameraError("in_use" if self.kind == "in_use" else "not_installed")


@pytest.mark.parametrize("kind", ["in_use", "not_installed"])
def test_unavailable_camera_exits_with_code_4(
    data_home: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    monkeypatch.setattr(service_module, "UNAVAILABLE_GRACE_S", 0.2)
    paths = DataPaths.resolve().ensure()
    code = run_service(paths, _BrokenSink(kind), pipe_name(suffix=f"t-{uuid.uuid4().hex}"))
    assert code == VirtualCameraError.exit_code == 4
