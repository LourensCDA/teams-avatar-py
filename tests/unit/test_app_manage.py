"""Library management through the facade (FR-005, FR-013, FR-016)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from teams_avatar.app import App
from teams_avatar.errors import InUseError, NameClashError, NotFoundError
from teams_avatar.paths import DataPaths
from tests.conftest import ClipFactory
from tests.fakes import FakeClock, FakeControlChannel, FakeLauncher


class Env:
    def __init__(self, data_home: Path, make_clip: ClipFactory) -> None:
        self.paths = DataPaths.resolve().ensure()
        self.presenting_id: str | None = None
        self.channel = FakeControlChannel(self.reply)
        self.clock = FakeClock()
        self.app = App(
            self.paths,
            control=self.channel,
            launcher=FakeLauncher(self.channel),
            clock=self.clock,
            sleep=self.clock.advance,
        )
        src = make_clip(data_home.parent / "src.mp4", 60, 320, 180)
        self.a = self.app.import_file(src, "Alpha").recording
        self.b = self.app.import_file(src, "Beta").recording
        self.c = self.app.import_file(src, "Gamma").recording

    def reply(self, req: dict[str, Any]) -> dict[str, Any]:
        presenting = self.presenting_id is not None
        return {
            "ok": True,
            "status": {
                "service_running": True,
                "presenting": presenting,
                "recording_id": self.presenting_id,
                "recording_name": "Alpha" if presenting else None,
                "canvas_width": 1280,
                "canvas_height": 720,
                "quality": "720p",
                "indicator_on": False,
            },
        }

    def present(self, rid: str) -> None:
        self.channel.running = True
        self.presenting_id = rid


@pytest.fixture
def env(data_home: Path, make_clip: ClipFactory) -> Env:
    return Env(data_home, make_clip)


def test_list_marks_presenting_and_missing(env: Env) -> None:
    env.present(env.a.id)
    env.app.paths.recording_file(env.c.id).unlink()
    views = {v.recording.name: v for v in env.app.list_recordings()}
    assert views["Alpha"].presenting and not views["Alpha"].missing
    assert not views["Beta"].presenting and not views["Beta"].missing
    assert views["Gamma"].missing


def test_list_when_service_not_running(env: Env) -> None:
    assert not any(v.presenting for v in env.app.list_recordings())


def test_rename_and_delete_refused_while_presenting(env: Env) -> None:
    env.present(env.a.id)
    with pytest.raises(InUseError, match="rename"):
        env.app.rename("Alpha", "New")
    with pytest.raises(InUseError, match="delete"):
        env.app.delete("alpha")
    assert env.app.paths.recording_file(env.a.id).exists()
    assert env.app.rename("Beta", "Beta 2").name == "Beta 2"


def test_rename_clash(env: Env) -> None:
    with pytest.raises(NameClashError):
        env.app.rename("Alpha", "beta")


def test_delete_removes_entry_file_and_default(env: Env) -> None:
    env.app.set_default("Beta")
    env.app.delete("Beta")
    assert not env.app.paths.recording_file(env.b.id).exists()
    names = [r.name for r in env.app.library.load()]
    assert names == ["Alpha", "Gamma"]
    assert not any(r.is_default for r in env.app.library.load())
    with pytest.raises(NotFoundError):
        env.app.delete("Beta")


def test_missing_entry_can_be_deleted(env: Env) -> None:
    env.app.paths.recording_file(env.c.id).unlink()
    env.app.delete("Gamma")
    assert [r.name for r in env.app.library.load()] == ["Alpha", "Beta"]


def test_set_default_clears_previous(env: Env) -> None:
    env.app.set_default("Alpha")
    rec = env.app.set_default("Gamma")
    assert rec.is_default and rec.name == "Gamma"
    assert [r.name for r in env.app.library.load() if r.is_default] == ["Gamma"]


def test_activity_records(env: Env) -> None:
    env.app.rename("Alpha", "Alpha 2")
    env.app.delete("Beta")
    events = [(r.event, r.recording_name, r.outcome) for r in env.app.history(2)]
    assert events == [("renamed", "Alpha 2", "renamed"), ("deleted", "Beta", "deleted")]
    renamed = env.app.history(2)[0]
    assert renamed.recording_id == env.a.id and renamed.detail and "Alpha" in renamed.detail
