import errno
import hashlib
import os
import shutil
from datetime import datetime
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import pytest

from teams_avatar import importer
from teams_avatar.activity import ActivityLog
from teams_avatar.adapters.pyav_codec import PyAVReader, PyAVWriter, VideoInfo, probe
from teams_avatar.errors import (
    ImportRejectedError,
    LibraryWriteError,
    NameClashError,
    StorageFullError,
    UnreadableVideoError,
)
from teams_avatar.importer import import_file
from teams_avatar.library import Library
from teams_avatar.models import Recording, RecordResult
from teams_avatar.paths import DataPaths
from tests.clips import counter_frame, read_counter
from tests.conftest import ClipFactory
from tests.contract.test_pyav_codec import make_rotated, make_vfr

NOW = datetime(2026, 11, 11, 10, 0, 0)


@pytest.fixture
def paths(data_home: Path) -> DataPaths:
    return DataPaths.resolve().ensure()


def do_import(paths: DataPaths, source: Path, name: str | None = None) -> RecordResult:
    return import_file(paths, Library(paths), ActivityLog(paths), source, name, now=lambda: NOW)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_with_audio(path: Path, frames: int, fps: int) -> Path:
    with av.open(str(path), "w") as c:
        v = c.add_stream("libx264", rate=fps)
        v.width, v.height, v.pix_fmt = 320, 180, "yuv420p"
        a = c.add_stream("aac", rate=48000)
        for i in range(frames):
            for p in v.encode(av.VideoFrame.from_ndarray(counter_frame(i, 320, 180), "bgr24")):
                c.mux(p)
        samples = np.zeros((1, 1024), dtype=np.float32)
        for k in range(frames * 48000 // fps // 1024):
            af = av.AudioFrame.from_ndarray(samples, format="fltp", layout="mono")
            af.sample_rate = 48000
            af.pts = k * 1024
            af.time_base = Fraction(1, 48000)
            for p in a.encode(af):
                c.mux(p)
        for p in v.encode():
            c.mux(p)
        for p in a.encode():
            c.mux(p)
    return path


def test_valid_import(paths: DataPaths, tmp_path: Path, make_clip: ClipFactory) -> None:
    src = make_clip(tmp_path / "My clip.mp4", 180, 640, 360)
    before = sha(src)
    result = do_import(paths, src, "Office")
    rec = result.recording
    assert sha(src) == before  # original untouched (FR-021)
    stored = paths.recording_file(rec.id)
    assert stored.exists()
    assert not paths.partial_file(rec.id).exists()
    info = probe(stored)
    assert (info.width, info.height, info.frame_count) == (640, 360, 180)
    assert info.fps == pytest.approx(30)
    assert rec.name == "Office"
    assert rec.origin == "imported"
    assert rec.source_name == "My clip.mp4"
    assert rec.frame_count == 180
    assert rec.duration_s == pytest.approx(6.0, abs=0.05)
    assert rec.size_bytes == stored.stat().st_size
    assert Library(paths).find("office").id == rec.id
    events = ActivityLog(paths).read_last(5)
    assert [(e.event, e.recording_id, e.outcome) for e in events] == [("imported", rec.id, "saved")]


def test_default_name(paths: DataPaths, tmp_path: Path, make_clip: ClipFactory) -> None:
    src = make_clip(tmp_path / "a.mp4", 30, 320, 180)
    assert do_import(paths, src).recording.name == "Recording 2026-11-11 10-00-00"


def test_resampled_to_30_fps(paths: DataPaths, tmp_path: Path, make_clip: ClipFactory) -> None:
    src = make_clip(tmp_path / "slow.mp4", 45, 320, 180, fps=15)
    rec = do_import(paths, src).recording
    info = probe(paths.recording_file(rec.id))
    assert info.fps == pytest.approx(30)
    assert info.frame_count == pytest.approx(90, abs=1)
    assert rec.duration_s == pytest.approx(3.0, abs=0.05)


def test_size_capped(paths: DataPaths, tmp_path: Path, make_clip: ClipFactory) -> None:
    src = make_clip(tmp_path / "big.mp4", 10, 2560, 1440)
    rec = do_import(paths, src).recording
    assert (rec.width, rec.height) == (1920, 1080)


def test_audio_dropped(paths: DataPaths, tmp_path: Path) -> None:
    src = write_with_audio(tmp_path / "talk.mp4", 60, 30)
    rec = do_import(paths, src).recording
    with av.open(str(paths.recording_file(rec.id))) as c:
        assert len(c.streams.audio) == 0
    with PyAVReader(paths.recording_file(rec.id)) as r:
        assert r.frame_count == 60


def test_warnings_computed(paths: DataPaths, tmp_path: Path, make_clip: ClipFactory) -> None:
    src = make_clip(tmp_path / "short.mp4", 60, 320, 180, distinct=False)
    result = do_import(paths, src)
    assert result.warnings == ["too_short"]
    assert result.recording.warnings == ["too_short"]


def test_written_as_partial_then_renamed(
    paths: DataPaths, tmp_path: Path, make_clip: ClipFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = make_clip(tmp_path / "a.mp4", 30, 320, 180)
    moves: list[tuple[str, str]] = []
    real = os.replace

    def spy(a: str, b: str) -> None:
        moves.append((os.fspath(a), os.fspath(b)))
        real(a, b)

    monkeypatch.setattr(os, "replace", spy)
    rec = do_import(paths, src).recording
    assert (str(paths.partial_file(rec.id)), str(paths.recording_file(rec.id))) in moves


@pytest.mark.parametrize("kind", ["text", "truncated", "missing"])
def test_rejected_files_leave_library_unchanged(
    paths: DataPaths, tmp_path: Path, make_clip: ClipFactory, kind: str
) -> None:
    if kind == "text":
        src = tmp_path / "notes.mp4"
        src.write_text("hello", encoding="utf-8")
    elif kind == "truncated":
        good = make_clip(tmp_path / "good.mp4", 90, 320, 180)
        src = tmp_path / "cut.mp4"
        src.write_bytes(good.read_bytes()[: good.stat().st_size // 2])
    else:
        src = tmp_path / "nope.mp4"
    with pytest.raises(ImportRejectedError) as info:
        do_import(paths, src, "X")
    assert info.value.reason
    assert Library(paths).load() == []
    assert list(paths.recordings.iterdir()) == []
    events = ActivityLog(paths).read_last(5)
    assert [e.event for e in events] == ["import_rejected"]
    assert events[0].detail


def test_name_clash_replaces_nothing(
    paths: DataPaths, tmp_path: Path, make_clip: ClipFactory
) -> None:
    src = make_clip(tmp_path / "a.mp4", 30, 320, 180)
    first = do_import(paths, src, "Office").recording
    files_before = sorted(p.name for p in paths.recordings.iterdir())
    with pytest.raises(NameClashError):
        do_import(paths, src, "OFFICE")
    assert sorted(p.name for p in paths.recordings.iterdir()) == files_before
    assert [r.id for r in Library(paths).load()] == [first.id]


def test_rotated_phone_video_imported_upright(
    paths: DataPaths, tmp_path: Path, make_clip: ClipFactory
) -> None:
    base = make_clip(tmp_path / "land.mp4", 40, 640, 360)
    src = make_rotated(base, tmp_path / "phone.mp4", 90)
    rec = do_import(paths, src).recording
    assert (rec.width, rec.height) == (360, 640)
    with PyAVReader(paths.recording_file(rec.id)) as r:
        r.seek(7)
        frame = r.read()
    assert frame is not None
    assert read_counter(np.rot90(frame, -1)) == 7


def test_variable_frame_rate_keeps_duration(paths: DataPaths, tmp_path: Path) -> None:
    rec = do_import(paths, make_vfr(tmp_path)).recording
    # 1 s at 30 fps then 1 s at 10 fps: index / average fps would give a different length.
    assert rec.duration_s == pytest.approx(2.0, abs=0.1)
    with PyAVReader(paths.recording_file(rec.id)) as r:
        counters = []
        while (f := r.read()) is not None:
            counters.append(read_counter(f))
    # Each slow-segment frame (counter 0, 1, ...) lasts three output frames.
    assert counters[30:36] == [0, 0, 0, 1, 1, 1]


def test_encoder_failure_is_not_blamed_on_source(
    paths: DataPaths,
    tmp_path: Path,
    make_clip: ClipFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = make_clip(tmp_path / "a.mp4", 30, 320, 180)
    real = PyAVWriter.write
    calls = {"n": 0}
    failure: list[Exception] = [OSError(errno.ENOSPC, "No space left on device")]

    def flaky(self: PyAVWriter, frame: np.ndarray) -> None:
        calls["n"] += 1
        if calls["n"] == 5:
            raise failure[0]
        real(self, frame)

    monkeypatch.setattr(PyAVWriter, "write", flaky)
    with pytest.raises(StorageFullError):
        do_import(paths, src, "A")
    calls["n"] = 0
    failure[0] = PermissionError("denied")
    with pytest.raises(LibraryWriteError):
        do_import(paths, src, "B")
    assert list(paths.recordings.iterdir()) == []
    assert Library(paths).load() == []
    events = ActivityLog(paths).read_last(5)
    assert [e.event for e in events] == ["import_rejected"] * 2
    assert all("library could not be written" in (e.detail or "") for e in events)


def test_replace_permission_error_is_reported(
    paths: DataPaths, tmp_path: Path, make_clip: ClipFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = make_clip(tmp_path / "a.mp4", 30, 320, 180)

    def deny(a: str, b: str) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(os, "replace", deny)
    with pytest.raises(LibraryWriteError):
        do_import(paths, src)
    assert list(paths.recordings.iterdir()) == []
    assert [e.event for e in ActivityLog(paths).read_last(5)] == ["import_rejected"]


def test_unreadable_stored_file_becomes_rejection(
    paths: DataPaths, tmp_path: Path, make_clip: ClipFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(path: Path) -> None:
        raise UnreadableVideoError("the video contains no frames")

    monkeypatch.setattr(importer, "probe", broken)
    src = make_clip(tmp_path / "a.mp4", 30, 320, 180)
    with pytest.raises(ImportRejectedError):
        do_import(paths, src)
    assert list(paths.recordings.iterdir()) == []
    assert Library(paths).load() == []
    assert [e.event for e in ActivityLog(paths).read_last(5)] == ["import_rejected"]


def test_recordings_folder_created_before_space_check(
    paths: DataPaths, tmp_path: Path, make_clip: ClipFactory
) -> None:
    shutil.rmtree(paths.recordings)
    src = make_clip(tmp_path / "a.mp4", 30, 320, 180)
    assert do_import(paths, src).recording.frame_count == 30


def test_frame_size_checked_per_frame() -> None:
    odd = np.zeros((90, 160, 3), dtype=np.uint8)
    assert importer._fit(odd, 320, 180).shape == (180, 320, 3)
    assert importer._fit(odd, 160, 90) is odd


def test_name_taken_during_transcode_keeps_import_under_next_free_name(
    paths: DataPaths, tmp_path: Path, make_clip: ClipFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = make_clip(tmp_path / "a.mp4", 30, 320, 180)
    real_transcode = importer._transcode

    def transcode_then_race(source: Path, partial: Path) -> None:
        real_transcode(source, partial)
        Library(paths).add(
            RecordResultFactory.recording("Office", "c" * 32)  # another process wins the name
        )

    monkeypatch.setattr(importer, "_transcode", transcode_then_race)
    result = do_import(paths, src, "Office")
    assert result.recording.name == "Office 2"
    assert paths.recording_file(result.recording.id).exists()


class RecordResultFactory:
    @staticmethod
    def recording(name: str, rid: str) -> Recording:
        from datetime import UTC

        return Recording(
            id=rid,
            name=name,
            origin="imported",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            duration_s=1.0,
            frame_count=30,
            width=320,
            height=180,
            size_bytes=1,
        )


def test_activity_failure_after_import_keeps_result(
    paths: DataPaths, tmp_path: Path, make_clip: ClipFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = make_clip(tmp_path / "a.mp4", 30, 320, 180)

    def boom(self: ActivityLog, record: object) -> None:
        raise OSError("log locked")

    monkeypatch.setattr(ActivityLog, "append", boom)
    result = do_import(paths, src, "Fine")
    assert result.recording.name == "Fine"
    assert [r.name for r in Library(paths).load()] == ["Fine"]


def test_activity_failure_on_rejection_keeps_real_error(
    paths: DataPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")

    def boom(self: ActivityLog, record: object) -> None:
        raise OSError("log locked")

    monkeypatch.setattr(ActivityLog, "append", boom)
    with pytest.raises(ImportRejectedError):
        do_import(paths, bad)


def test_import_after_crash_recovery_registered_the_file_succeeds(
    paths: DataPaths, tmp_path: Path, make_clip: ClipFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = make_clip(tmp_path / "a.mp4", 30, 320, 180)
    real_probe = probe

    def recovered_first(path: Path) -> VideoInfo:
        if not path.name.endswith(".partial.mp4") and path.parent == paths.recordings:
            Library(paths).add(RecordResultFactory.recording("Recovered 1", path.stem))
        return real_probe(path)

    monkeypatch.setattr(importer, "probe", recovered_first)
    result = do_import(paths, src, "Office")
    assert result.recording.name == "Office"
    assert [r.name for r in Library(paths).load()] == ["Office"]
    assert paths.recording_file(result.recording.id).exists()
