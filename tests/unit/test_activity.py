import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from teams_avatar.activity import EVENTS, ActivityLog
from teams_avatar.models import ActivityRecord
from teams_avatar.paths import DataPaths


@pytest.fixture
def log(data_home: Path) -> ActivityLog:
    paths = DataPaths.resolve()
    paths.ensure()
    return ActivityLog(paths)


def record(event: str = "imported", n: int = 0) -> ActivityRecord:
    return ActivityRecord(
        at=datetime(2026, 11, 11, 12, 0, n, tzinfo=UTC),
        event=event,
        recording_id="r" * 32,
        recording_name=f"Clip {n}",
        session_id=None,
        outcome="saved",
        detail=None,
    )


def test_events_enum() -> None:
    assert set(EVENTS) == {
        "recorded",
        "imported",
        "presented",
        "presentation_ended",
        "renamed",
        "deleted",
        "record_failed",
        "import_rejected",
    }


def test_one_json_object_per_line(log: ActivityLog) -> None:
    log.append(record(n=1))
    log.append(record("deleted", n=2))
    lines = log.paths.activity.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert set(first) == {
        "at",
        "event",
        "recording_id",
        "recording_name",
        "session_id",
        "outcome",
        "detail",
    }
    assert first["event"] == "imported"


def test_read_last_n_in_order(log: ActivityLog) -> None:
    for i in range(5):
        log.append(record(n=i))
    last = log.read_last(3)
    assert [r.recording_name for r in last] == ["Clip 2", "Clip 3", "Clip 4"]
    assert last[0] == record(n=2)


def test_read_when_empty(log: ActivityLog) -> None:
    assert log.read_last(20) == []


def test_unknown_event_refused(log: ActivityLog) -> None:
    with pytest.raises(ValueError):
        log.append(record("hacked"))


@pytest.mark.parametrize("value", [b"\x00\x01", bytearray(b"ab"), np.zeros((2, 2)), [1, 2]])
def test_no_picture_content(log: ActivityLog, value: object) -> None:
    bad = record()
    object.__setattr__(bad, "detail", value)
    with pytest.raises(TypeError):
        log.append(bad)
    assert not log.paths.activity.exists() or log.paths.activity.read_text() == ""
