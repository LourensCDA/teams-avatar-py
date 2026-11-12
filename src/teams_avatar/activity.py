"""Activity log ``activity.jsonl``: one JSON object per line (FR-016), never picture content."""

from __future__ import annotations

import json
from collections import deque

from teams_avatar.locks import DataLock
from teams_avatar.models import ActivityRecord
from teams_avatar.paths import DataPaths

EVENTS = (
    "recorded",
    "imported",
    "presented",
    "presentation_ended",
    "renamed",
    "deleted",
    "record_failed",
    "import_rejected",
)


def _check(record: ActivityRecord) -> None:
    """Refuse unknown events and any field that is not text (no frames, bytes or arrays)."""
    if record.event not in EVENTS:
        raise ValueError(f"unknown activity event {record.event!r}")
    for name in ("event", "outcome", "recording_id", "recording_name", "session_id", "detail"):
        value = getattr(record, name)
        if value is not None and not isinstance(value, str):
            raise TypeError(f"activity field {name} must be text, not {type(value).__name__}")


class ActivityLog:
    """Append to and read from ``activity.jsonl``."""

    def __init__(self, paths: DataPaths) -> None:
        """Bind the log to a data folder."""
        self.paths = paths

    def append(self, record: ActivityRecord) -> None:
        """Append one record under ``data.lock`` (the CLI and the service both write).

        Raises:
            ValueError: If the event is unknown.
            TypeError: If a field holds anything other than text.
        """
        _check(record)
        line = json.dumps(record.to_dict(), ensure_ascii=False)
        with DataLock(self.paths.data_lock):
            with self.paths.activity.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def read_last(self, limit: int) -> list[ActivityRecord]:
        """Return the last ``limit`` records, oldest first; unparseable lines are skipped."""
        if limit <= 0 or not self.paths.activity.exists():
            return []
        with self.paths.activity.open(encoding="utf-8") as handle:
            lines = deque((line for line in handle if line.strip()), maxlen=limit)
        records: list[ActivityRecord] = []
        for line in lines:
            try:
                records.append(ActivityRecord.from_dict(json.loads(line)))
            except (ValueError, KeyError, TypeError):
                continue
        return records
