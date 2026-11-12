from datetime import datetime
from pathlib import Path

import pytest

from teams_avatar.library import Library
from teams_avatar.models import Recording
from teams_avatar.paths import DataPaths
from teams_avatar.reconcile import reconcile
from tests.conftest import ClipFactory

NOW = datetime(2026, 11, 11, 9, 30, 15)


def _LATER() -> float:
    """A clock far enough ahead that every orphan on disk is past the grace period."""
    import time

    return time.time() + 3600


@pytest.fixture
def paths(data_home: Path) -> DataPaths:
    p = DataPaths.resolve()
    p.ensure()
    return p


def test_stale_partial_deleted(paths: DataPaths) -> None:
    stale = paths.partial_file("a" * 32)
    stale.write_bytes(b"half a video")
    report = reconcile(paths, Library(paths), now=NOW, clock=_LATER)
    assert not stale.exists()
    assert report.deleted_partials == [stale]


def test_partial_held_by_running_recording_kept(paths: DataPaths) -> None:
    held = paths.partial_file("b" * 32)
    with held.open("wb") as handle:  # an open writer holds the file on Windows
        handle.write(b"x")
        report = reconcile(paths, Library(paths), now=NOW)
        assert held.exists()
    assert report.deleted_partials == []


def test_orphan_video_recovered_once(paths: DataPaths, make_clip: ClipFactory) -> None:
    rid = "c" * 32
    make_clip(paths.recording_file(rid), 60, 320, 180, distinct=False)
    lib = Library(paths)
    report = reconcile(paths, lib, now=NOW, clock=_LATER)
    assert [r.id for r in report.recovered] == [rid]
    entry = lib.find(rid[:8])
    assert entry.origin == "imported"
    assert entry.name == "Recovered 2026-11-11 09-30-15"
    assert entry.frame_count == 60
    assert (entry.width, entry.height) == (320, 180)
    assert entry.size_bytes == paths.recording_file(rid).stat().st_size
    assert entry.warnings == ["too_short"]
    again = reconcile(paths, lib, now=NOW, clock=_LATER)
    assert again.recovered == []


def test_missing_file_entries_kept_and_flagged(paths: DataPaths) -> None:
    from datetime import UTC

    rid = "d" * 32
    lib = Library(paths)
    lib.add(
        Recording(
            id=rid,
            name="Gone",
            origin="recorded",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            duration_s=10.0,
            frame_count=300,
            width=1280,
            height=720,
            size_bytes=1,
        )
    )
    report = reconcile(paths, lib, now=NOW)
    assert report.missing == [rid]
    assert lib.find("Gone").id == rid


def test_unreadable_orphan_left_alone(paths: DataPaths) -> None:
    junk = paths.recording_file("e" * 32)
    junk.write_bytes(b"not video")
    report = reconcile(paths, Library(paths), now=NOW)
    assert report.recovered == []
    assert junk.exists()


def test_fresh_orphan_not_recovered_within_grace(paths: DataPaths, make_clip: ClipFactory) -> None:
    """A just-renamed video may belong to a session still about to call library.add."""
    rid = "9" * 32
    make_clip(paths.recording_file(rid), 60, 320, 180, distinct=False)
    lib = Library(paths)
    report = reconcile(
        paths, lib, now=NOW, clock=lambda: paths.recording_file(rid).stat().st_mtime + 10
    )
    assert report.recovered == []
    assert lib.load() == []


def test_old_orphan_recovered_after_grace(paths: DataPaths, make_clip: ClipFactory) -> None:
    rid = "8" * 32
    make_clip(paths.recording_file(rid), 60, 320, 180, distinct=False)
    lib = Library(paths)
    mtime = paths.recording_file(rid).stat().st_mtime
    report = reconcile(paths, lib, now=NOW, clock=lambda: mtime + 301)
    assert [r.id for r in report.recovered] == [rid]


def test_fresh_closed_partial_kept_within_grace(paths: DataPaths) -> None:
    # An import closes its partial a moment before renaming it; it must not be deleted then.
    fresh = paths.partial_file("d" * 32)
    fresh.write_bytes(b"just finished writing")
    report = reconcile(paths, Library(paths), now=NOW)
    assert fresh.exists()
    assert report.deleted_partials == []


class _RacingLibrary(Library):
    """A library where another process registers each recording just before this one does."""

    def __init__(self, paths: DataPaths, error: Exception) -> None:
        super().__init__(paths)
        self.error = error

    def add_renaming(self, recording: Recording) -> Recording:
        raise self.error


def test_orphan_registered_concurrently_is_skipped(
    paths: DataPaths, make_clip: ClipFactory
) -> None:
    from teams_avatar.errors import DuplicateIdError

    rid = "e" * 32
    make_clip(paths.recording_file(rid), 60, 320, 180, distinct=False)
    lib = _RacingLibrary(paths, DuplicateIdError(rid))
    report = reconcile(paths, lib, now=NOW, clock=_LATER)
    assert report.recovered == []
    assert paths.recording_file(rid).exists()


def test_recovered_name_taken_concurrently_gets_suffix(
    paths: DataPaths, make_clip: ClipFactory
) -> None:
    rid = "f" * 32
    make_clip(paths.recording_file(rid), 60, 320, 180, distinct=False)
    lib = Library(paths)
    real_add_renaming = lib.add_renaming

    def other_process_first(recording: Recording) -> Recording:
        # Another start recovered a different orphan under the same name a moment earlier.
        Library(paths).add(Recording.from_dict(dict(recording.to_dict(), id="0" * 32)))
        return real_add_renaming(recording)

    lib.add_renaming = other_process_first  # type: ignore[method-assign]
    report = reconcile(paths, lib, now=NOW, clock=_LATER)
    assert [r.name for r in report.recovered] == ["Recovered 2026-11-11 09-30-15 2"]
