"""Startup reconciliation after crashes (research R8, FR-004)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from teams_avatar.adapters.pyav_codec import probe
from teams_avatar.analysis import compute_warnings
from teams_avatar.errors import DuplicateIdError, TeamsAvatarError
from teams_avatar.library import Library
from teams_avatar.models import Recording
from teams_avatar.paths import DataPaths

log = logging.getLogger(__name__)

PARTIAL_SUFFIX = ".partial.mp4"
# A session renames its video to <id>.mp4 a moment before it registers it in the library; crash
# recovery does not need to be instant, so younger orphans are left for a later start.
ORPHAN_GRACE_S = 300.0


@dataclass
class ReconcileReport:
    """What reconciliation changed or found, so front ends can report it once."""

    deleted_partials: list[Path] = field(default_factory=list)
    recovered: list[Recording] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def _is_recording_id(stem: str) -> bool:
    """Return True if ``stem`` looks like one of our UUID4-hex ids."""
    return len(stem) == 32 and all(c in "0123456789abcdef" for c in stem)


def reconcile(
    paths: DataPaths,
    library: Library,
    now: datetime | None = None,
    clock: Callable[[], float] = time.time,
) -> ReconcileReport:
    """Clean up after an interrupted recording, import or metadata write.

    - ``*.partial.mp4`` files are deleted unless a running writer still holds them open
      (Windows refuses the delete) or they were modified in the last 5 minutes.
    - An ``<id>.mp4`` with no library entry is added back as an imported recording named
      ``Recovered YYYY-MM-DD HH-MM-SS`` (``... 2`` if taken); unreadable ones are left for the
      user to inspect, and files modified in the last 5 minutes are skipped (a live session may
      be about to add them).
    - Entries whose file is missing are kept and reported as ``missing``.

    Args:
        paths: Data folder.
        library: Library to repair.
        now: Local time used for recovered names (defaults to now).
        clock: Epoch-seconds clock compared with file modification times (test seam).

    Returns:
        The changes made.
    """
    report = ReconcileReport()
    if not paths.recordings.is_dir():
        return report
    for partial in sorted(paths.recordings.glob(f"*{PARTIAL_SUFFIX}")):
        try:
            # An import closes its partial a moment before renaming it, so the open-file check
            # alone cannot tell it from a stale one.
            if clock() - partial.stat().st_mtime < ORPHAN_GRACE_S:
                continue
            partial.unlink()
        except OSError:
            continue
        report.deleted_partials.append(partial)

    known = {r.id for r in library.load()}
    local_now = now or datetime.now()
    for video in sorted(paths.recordings.glob("*.mp4")):
        if video.name.endswith(PARTIAL_SUFFIX) or not _is_recording_id(video.stem):
            continue
        if video.stem in known:
            continue
        try:
            if clock() - video.stat().st_mtime < ORPHAN_GRACE_S:
                continue
        except OSError:
            continue
        try:
            info = probe(video)
            warnings = compute_warnings(video)
        except TeamsAvatarError as exc:
            log.warning("orphaned video %s is unreadable: %s", video.name, exc.message)
            continue
        recovered = Recording(
            id=video.stem,
            name=local_now.strftime("Recovered %Y-%m-%d %H-%M-%S"),
            origin="imported",
            created_at=datetime.now(UTC),
            duration_s=info.duration_s,
            frame_count=info.frame_count,
            width=info.width,
            height=info.height,
            size_bytes=video.stat().st_size,
            warnings=warnings,
        )
        try:
            # Another command starting at the same moment may recover the same file or take the
            # same name; neither may make this command fail.
            stored = library.add_renaming(recovered)
        except DuplicateIdError:
            continue
        report.recovered.append(stored)

    report.missing = [r.id for r in library.load() if not paths.recording_file(r.id).exists()]
    return report
