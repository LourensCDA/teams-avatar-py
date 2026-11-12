import os
import time
from pathlib import Path

from teams_avatar.app import App
from teams_avatar.paths import DataPaths


def test_notice_until_acknowledged(data_home: Path) -> None:
    app = App(DataPaths.resolve())
    notice = app.first_use_notice()
    assert notice is not None
    lowered = notice.lower()
    assert "indicator" in lowered and "off by default" in lowered
    assert "responsib" in lowered
    assert "participants" in lowered or "tell" in lowered
    assert "rules" in lowered
    settings = app.acknowledge_first_use()
    assert settings.first_use_acknowledged
    assert app.first_use_notice() is None
    assert App(DataPaths.resolve()).first_use_notice() is None


def test_history_empty_then_limited(data_home: Path) -> None:
    from datetime import UTC, datetime

    from teams_avatar.models import ActivityRecord

    app = App(DataPaths.resolve())
    assert app.history(20) == []
    for i in range(3):
        app.activity.append(
            ActivityRecord(
                at=datetime(2026, 1, 1, 0, 0, i, tzinfo=UTC),
                event="deleted",
                recording_id=None,
                recording_name=f"R{i}",
                session_id=None,
                outcome="deleted",
                detail=None,
            )
        )
    assert [r.recording_name for r in app.history(2)] == ["R1", "R2"]


def test_reconciliation_runs_once_per_process(data_home: Path) -> None:
    paths = DataPaths.resolve()
    paths.ensure()
    stale = paths.partial_file("f" * 32)
    stale.write_bytes(b"x")
    hour_ago = time.time() - 3600
    os.utime(stale, (hour_ago, hour_ago))  # older than the reconcile grace period
    app = App(paths)
    assert not stale.exists()
    assert app.startup_report is not None
    assert app.startup_report.deleted_partials == [stale]
