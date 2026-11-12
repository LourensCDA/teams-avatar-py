"""Recording session with a fake camera, fake window and fake clock (FR-001, FR-003, FR-004)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from teams_avatar.activity import ActivityLog
from teams_avatar.adapters.pyav_codec import PyAVWriter, VideoInfo, probe
from teams_avatar.errors import (
    CameraUnavailableError,
    LibraryWriteError,
    NameClashError,
    StorageFullError,
)
from teams_avatar.library import Library
from teams_avatar.models import CameraInfo, Recording
from teams_avatar.paths import DataPaths
from teams_avatar.recording import MIN_FREE_BYTES, RecordingSession
from tests.fakes import FakeCameraSource, FakeClock, FakeRecordingUI, Frame

CAM = CameraInfo(name="Integrated Camera", device_name="Integrated Camera")
NOW = datetime(2026, 11, 11, 8, 15, 0)
GB = 10**9


class Space:
    def __init__(self, free: int = 50 * GB) -> None:
        self.free = free
        self.checks = 0

    def __call__(self, path: Path) -> int:
        self.checks += 1
        return self.free


class Env:
    def __init__(self, data_home: Path, source: FakeCameraSource, space: Space) -> None:
        self.paths = DataPaths.resolve().ensure()
        self.library = Library(self.paths)
        self.activity = ActivityLog(self.paths)
        self.source = source
        self.space = space
        self.clock = FakeClock()

    def session(self) -> RecordingSession:
        return RecordingSession(
            self.paths,
            self.library,
            self.activity,
            self.source,
            clock=self.clock,
            free_bytes=self.space,
            now=lambda: NOW,
            frame_interval_s=1 / 30,
        )

    def files(self) -> list[str]:
        return sorted(p.name for p in self.paths.recordings.iterdir())


@pytest.fixture
def env(data_home: Path) -> Env:
    return Env(data_home, FakeCameraSource(), Space())


def test_save_after_space(env: Env) -> None:
    ui = FakeRecordingUI("save", after=180)
    result = env.session().run(None, CAM, ui)
    assert result is not None
    rec = result.recording
    assert rec.name == "Recording 2026-11-11 08-15-00"
    assert rec.origin == "recorded"
    assert (rec.width, rec.height) == (320, 180)
    assert rec.frame_count == 180
    info = probe(env.paths.recording_file(rec.id))
    assert info.frame_count == 180 and info.fps == pytest.approx(30)
    assert env.files() == [f"{rec.id}.mp4"]
    assert env.library.find(rec.name).id == rec.id
    assert env.source.closed and ui.closed_called
    assert ui.shown[0] == 0 and ui.shown[-1] == pytest.approx(179 / 30, abs=0.05)
    events = env.activity.read_last(5)
    assert [(e.event, e.outcome) for e in events] == [("recorded", "saved")]
    assert events[0].session_id and events[0].recording_id == rec.id


def test_warnings_computed(env: Env) -> None:
    result = env.session().run("Short", CAM, FakeRecordingUI("save", after=30))
    assert result is not None
    assert "too_short" in result.warnings


@pytest.mark.parametrize("key", ["cancel", "closed"])
def test_cancel_leaves_nothing(env: Env, key: str) -> None:
    assert env.session().run("X", CAM, FakeRecordingUI(key, after=40)) is None
    assert env.files() == []
    assert env.library.load() == []
    assert env.source.closed
    events = env.activity.read_last(5)
    assert [(e.event, e.outcome) for e in events] == [("record_failed", "cancelled")]


def test_camera_unavailable_creates_no_file(data_home: Path) -> None:
    err = CameraUnavailableError("Integrated Camera", "it is in use by another program")
    env = Env(data_home, FakeCameraSource(open_error=err), Space())
    with pytest.raises(CameraUnavailableError):
        env.session().run("X", CAM, FakeRecordingUI())
    assert env.files() == []
    events = env.activity.read_last(5)
    assert [(e.event, e.outcome) for e in events] == [("record_failed", "failed")]
    assert events[0].detail


def test_camera_failure_mid_recording_deletes_partial(data_home: Path) -> None:
    env = Env(data_home, FakeCameraSource(fail_after=20), Space())
    with pytest.raises(CameraUnavailableError):
        env.session().run("X", CAM, FakeRecordingUI("save", after=100))
    assert env.files() == []
    assert env.library.load() == []


def test_name_clash_checked_before_camera_opens(env: Env) -> None:
    from datetime import UTC

    env.library.add(
        Recording(
            id="a" * 32,
            name="Office",
            origin="recorded",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            duration_s=10.0,
            frame_count=300,
            width=320,
            height=180,
            size_bytes=1,
        )
    )
    with pytest.raises(NameClashError):
        env.session().run("office", CAM, FakeRecordingUI())
    assert env.source.opened == []


def test_low_space_before_start_refuses(data_home: Path) -> None:
    env = Env(data_home, FakeCameraSource(), Space(MIN_FREE_BYTES - 1))
    with pytest.raises(StorageFullError):
        env.session().run("X", CAM, FakeRecordingUI())
    assert env.source.opened == []


def test_low_space_during_recording_keeps_shorter_valid_recording(data_home: Path) -> None:
    space = Space()
    env = Env(data_home, FakeCameraSource(), space)

    class DropSpace(FakeRecordingUI):
        def show(self, frame: Frame, elapsed_s: float) -> None:
            super().show(frame, elapsed_s)
            if len(self.shown) == 90:
                space.free = MIN_FREE_BYTES - 1

    result = env.session().run("X", CAM, DropSpace("save", after=10_000))
    assert result is not None
    assert 90 <= result.recording.frame_count <= 130  # checked about once a second
    assert probe(env.paths.recording_file(result.recording.id)).frame_count == (
        result.recording.frame_count
    )


def test_minimum_free_space_is_200mb() -> None:
    assert MIN_FREE_BYTES == 200 * 1024 * 1024


def _events(env: Env) -> list[tuple[str, str]]:
    return [(e.event, e.outcome) for e in env.activity.read_last(10)]


def test_ctrl_c_logs_cancelled_and_cleans_up(env: Env) -> None:
    class Interrupt(FakeRecordingUI):
        def show(self, frame: Frame, elapsed_s: float) -> None:
            super().show(frame, elapsed_s)
            if len(self.shown) == 15:
                raise KeyboardInterrupt

    ui = Interrupt("save", after=100)
    with pytest.raises(KeyboardInterrupt):
        env.session().run("X", CAM, ui)
    assert env.files() == [] and env.library.load() == []
    assert env.source.closed and ui.closed_called
    last = env.activity.read_last(5)[-1]
    assert (last.event, last.outcome, last.detail) == ("record_failed", "cancelled", "interrupted")


def test_activity_failure_after_save_keeps_result(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(record: object) -> None:
        raise TimeoutError("lock")

    monkeypatch.setattr(env.activity, "append", boom)
    result = env.session().run("X", CAM, FakeRecordingUI("save", after=30))
    assert result is not None and env.library.find("X").id == result.recording.id


def test_activity_failure_keeps_original_error(
    data_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    err = CameraUnavailableError("c", "in use")
    env = Env(data_home, FakeCameraSource(open_error=err), Space())

    def boom(record: object) -> None:
        raise OSError("log broken")

    monkeypatch.setattr(env.activity, "append", boom)
    with pytest.raises(CameraUnavailableError):
        env.session().run("X", CAM, FakeRecordingUI())


def test_library_add_failure_logs_and_removes_file(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(rec: Recording) -> Recording:
        raise LibraryWriteError("disk says no")

    monkeypatch.setattr(env.library, "register", boom)
    with pytest.raises(LibraryWriteError):
        env.session().run("X", CAM, FakeRecordingUI("save", after=30))
    assert env.files() == []
    last = env.activity.read_last(5)[-1]
    assert (last.event, last.outcome) == ("record_failed", "failed")
    assert last.detail == "The recording could not be saved: disk says no"


def test_enospc_in_writer_chain_is_storage_full(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    import teams_avatar.recording as rec
    from teams_avatar.errors import UnreadableVideoError

    class Full(PyAVWriter):
        def write(self, frame: Frame) -> None:
            try:
                raise OSError(28, "No space left")
            except OSError as exc:
                raise UnreadableVideoError("encoding failed") from exc

    monkeypatch.setattr(rec, "PyAVWriter", Full)
    with pytest.raises(StorageFullError):
        env.session().run("X", CAM, FakeRecordingUI("save", after=30))
    assert env.files() == []


def test_frame_size_change_becomes_write_error(env: Env) -> None:
    original = env.source.read
    calls = {"n": 0}

    def shifting() -> Frame:
        calls["n"] += 1
        if calls["n"] == 5:
            env.source.width = 160
        return original()

    env.source.read = shifting  # type: ignore[method-assign]
    with pytest.raises(LibraryWriteError):
        env.session().run("X", CAM, FakeRecordingUI("save", after=30))
    assert env.files() == [] and _events(env) == [("record_failed", "failed")]


def test_unexpected_camera_open_error_is_translated(data_home: Path) -> None:
    env = Env(data_home, FakeCameraSource(open_error=RuntimeError("driver crash")), Space())
    with pytest.raises(CameraUnavailableError):
        env.session().run("X", CAM, FakeRecordingUI())
    assert _events(env) == [("record_failed", "failed")]
    assert env.source.closed


def test_name_taken_during_recording_keeps_video_under_next_free_name(env: Env) -> None:
    """Another process may claim the name while a long recording runs; the video must survive."""
    from datetime import UTC

    original_open = env.source.open

    def open_then_race(camera: CameraInfo) -> None:
        original_open(camera)
        env.library.add(
            Recording(
                id="b" * 32,
                name="Office",
                origin="recorded",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                duration_s=10.0,
                frame_count=300,
                width=320,
                height=180,
                size_bytes=1,
            )
        )

    env.source.open = open_then_race  # type: ignore[method-assign]
    result = env.session().run("Office", CAM, FakeRecordingUI("save", after=30))
    assert result is not None
    assert result.recording.name == "Office 2"
    assert env.paths.recording_file(result.recording.id).exists()
    assert sorted(r.name for r in env.library.load()) == ["Office", "Office 2"]


def _pre_register(paths: DataPaths, final: Path) -> None:
    """Simulate a concurrent start's crash recovery adopting the file before the session does."""
    from datetime import UTC

    Library(paths).add(
        Recording(
            id=final.stem,
            name="Recovered 2026-11-11 08-15-00",
            origin="imported",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            duration_s=1.0,
            frame_count=30,
            width=320,
            height=180,
            size_bytes=1,
        )
    )


def test_save_after_crash_recovery_registered_the_file_succeeds(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    from teams_avatar import recording

    real_probe = probe

    def recovered_first(path: Path) -> VideoInfo:
        _pre_register(env.paths, path)
        return real_probe(path)

    monkeypatch.setattr(recording, "probe", recovered_first)
    result = env.session().run("Office", CAM, FakeRecordingUI("save", after=30))
    assert result is not None
    assert result.recording.name == "Office"
    assert [r.name for r in env.library.load()] == ["Office"]
    assert env.paths.recording_file(result.recording.id).exists()
