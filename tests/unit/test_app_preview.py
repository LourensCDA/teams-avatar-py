"""Preview plays a recording once in a window and never touches the camera service (FR-005)."""

from __future__ import annotations

from pathlib import Path

import pytest

from teams_avatar.app import App
from teams_avatar.errors import NotFoundError, RecordingUnreadableError
from teams_avatar.paths import DataPaths
from tests.clips import read_counter
from tests.conftest import ClipFactory
from tests.fakes import FakeClock, FakeControlChannel, FakeLauncher, FakePlaybackUI


class Env:
    def __init__(self, data_home: Path, make_clip: ClipFactory, frames: int) -> None:
        self.paths = DataPaths.resolve().ensure()
        self.channel = FakeControlChannel()
        self.launcher = FakeLauncher(self.channel)
        self.clock = FakeClock()
        self.app = App(
            self.paths,
            control=self.channel,
            launcher=self.launcher,
            clock=self.clock,
            sleep=self.clock.advance,
        )
        src = make_clip(data_home.parent / "src.mp4", frames, 320, 180)
        self.rec = self.app.import_file(src, "Clip").recording


@pytest.fixture
def env(data_home: Path, make_clip: ClipFactory) -> Env:
    return Env(data_home, make_clip, 200)


def test_plays_once_with_end_blend_at_30fps(env: Env) -> None:
    ui = FakePlaybackUI()
    start = env.clock()
    env.app.preview("Clip", ui)
    assert len(ui.frames) == 200
    assert [read_counter(f) for f in ui.frames[:170]] == list(range(170))
    assert ui.frames[0].shape == (180, 320, 3)  # shown at the recording's own size
    assert env.clock() - start == pytest.approx(200 / 30, abs=0.1)
    assert ui.closed_called


def test_esc_closes_early(env: Env) -> None:
    ui = FakePlaybackUI(esc_after=20)
    env.app.preview("Clip", ui)
    assert len(ui.frames) == 20
    assert ui.closed_called


def test_never_touches_control_channel_or_service(env: Env) -> None:
    env.app.preview("Clip", FakePlaybackUI())
    assert env.channel.requests == []
    assert env.launcher.starts == 0


def test_unreadable_file(env: Env) -> None:
    env.paths.recording_file(env.rec.id).write_bytes(b"junk")
    with pytest.raises(RecordingUnreadableError, match="Clip"):
        env.app.preview("Clip", FakePlaybackUI())


def test_unknown(env: Env) -> None:
    with pytest.raises(NotFoundError):
        env.app.preview("nope", FakePlaybackUI())
