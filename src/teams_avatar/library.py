"""Recording library: ``library.json`` entries, name rules, default, lookup (FR-004, FR-005)."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime

from teams_avatar.errors import (
    DataFileError,
    DuplicateIdError,
    InvalidArgumentError,
    NameClashError,
    NotFoundError,
)
from teams_avatar.jsonfile import read_versioned, write_atomic
from teams_avatar.locks import DataLock
from teams_avatar.models import Recording
from teams_avatar.paths import DataPaths

NAME_PATTERN = re.compile(r"[A-Za-z0-9 _.\-]{1,64}")
MIN_ID_PREFIX = 6


def validate_name(name: str) -> str:
    """Return the trimmed name if it follows the name rules.

    Raises:
        InvalidArgumentError: If the trimmed name is empty, too long or has other characters.
    """
    trimmed = name.strip()
    if not NAME_PATTERN.fullmatch(trimmed):
        raise InvalidArgumentError(
            f'"{name}" is not a valid recording name',
            "Use 1-64 letters, digits, spaces, hyphens, underscores or full stops.",
        )
    return trimmed


def default_name(now: datetime) -> str:
    """Return the default name for a recording finished at local time ``now``."""
    return now.strftime("Recording %Y-%m-%d %H-%M-%S")


def find_in(recordings: list[Recording], ref: str) -> Recording:
    """Find a recording by name (case-insensitive) or unique id prefix of 6+ characters.

    Raises:
        NotFoundError: If nothing matches or the id prefix is ambiguous.
    """
    wanted = ref.strip().casefold()
    for rec in recordings:
        if rec.name.casefold() == wanted:
            return rec
    if len(wanted) >= MIN_ID_PREFIX:
        matches = [r for r in recordings if r.id.startswith(wanted)]
        if len(matches) == 1:
            return matches[0]
    raise NotFoundError(ref)


def _check_free(recordings: list[Recording], name: str, except_id: str | None = None) -> None:
    """Raise NameClashError if another recording already uses ``name``."""
    key = name.casefold()
    for rec in recordings:
        if rec.id != except_id and rec.name.casefold() == key:
            raise NameClashError(name)


def _check_id_unused(recordings: list[Recording], recording_id: str) -> None:
    """Raise DuplicateIdError if an entry already has ``recording_id``."""
    if any(r.id == recording_id for r in recordings):
        raise DuplicateIdError(recording_id)


def _next_free(recordings: list[Recording], name: str) -> str:
    """Return ``name``, or ``"<name> N"`` with the lowest N >= 2 no recording uses (<= 64 chars)."""
    taken = {r.name.casefold() for r in recordings}
    chosen = name
    counter = 2
    while chosen.casefold() in taken:
        suffix = f" {counter}"
        chosen = name[: 64 - len(suffix)].rstrip() + suffix
        counter += 1
    return chosen


class Library:
    """Load and change ``library.json``; every change is a locked read-modify-write."""

    def __init__(self, paths: DataPaths) -> None:
        """Bind the library to a data folder."""
        self.paths = paths

    def load(self) -> list[Recording]:
        """Return all recordings, validating the whole file first.

        Raises:
            DataFileError: If the file is unreadable or any entry is invalid.
        """
        data = read_versioned(self.paths.library)
        if data is None:
            return []
        entries = data.get("recordings")
        if not isinstance(entries, list):
            raise DataFileError(self.paths.library.name, "it has no list of recordings")
        try:
            recordings = [Recording.from_dict(entry) for entry in entries]
        except ValueError as exc:
            raise DataFileError(self.paths.library.name, f"an entry is invalid ({exc})") from exc
        if sum(r.is_default for r in recordings) > 1:
            raise DataFileError(self.paths.library.name, "more than one recording is the default")
        return recordings

    def _save(self, recordings: list[Recording]) -> None:
        write_atomic(self.paths.library, {"recordings": [r.to_dict() for r in recordings]})

    @contextmanager
    def edit(self) -> Iterator[list[Recording]]:
        """Yield the recordings under ``data.lock``; the list is saved if the block succeeds."""
        with DataLock(self.paths.data_lock):
            recordings = self.load()
            yield recordings
            self._save(recordings)

    def find(self, ref: str) -> Recording:
        """Find one recording by name or id prefix (see ``find_in``)."""
        return find_in(self.load(), ref)

    def check_name_free(self, name: str) -> str:
        """Validate ``name`` and make sure no recording uses it; return the trimmed name."""
        valid = validate_name(name)
        _check_free(self.load(), valid)
        return valid

    def add(self, recording: Recording) -> Recording:
        """Add a new recording; nothing is replaced on a name clash.

        Raises:
            InvalidArgumentError: If the name breaks the name rules.
            NameClashError: If the name is already used.
            DuplicateIdError: If a recording with the same id exists (never appended twice).
        """
        validate_name(recording.name)
        with self.edit() as recordings:
            _check_id_unused(recordings, recording.id)
            _check_free(recordings, recording.name)
            if recording.is_default:
                recordings[:] = [replace(r, is_default=False) for r in recordings]
            recordings.append(recording)
        return recording

    def add_renaming(self, recording: Recording) -> Recording:
        """Add a finished recording, using the next free ``"<name> N"`` if its name is taken.

        Used where the video already exists and must not be thrown away because another process
        claimed the name while it was being made.

        Returns:
            The stored entry (its name may differ from the requested one).

        Raises:
            InvalidArgumentError: If the name breaks the name rules.
            DuplicateIdError: If a recording with the same id exists.
        """
        validate_name(recording.name)
        with self.edit() as recordings:
            _check_id_unused(recordings, recording.id)
            stored = replace(recording, name=_next_free(recordings, recording.name))
            if stored.is_default:
                recordings[:] = [replace(r, is_default=False) for r in recordings]
            recordings.append(stored)
        return stored

    def register(self, recording: Recording) -> Recording:
        """Register a just-finished recording whose video file is already in place.

        Like ``add_renaming``, except that an entry with the same id (crash recovery in another
        command adopted the file a moment earlier) is taken over rather than refused: it gets
        this recording's details, and its name too when no other entry uses it.

        Returns:
            The stored entry.

        Raises:
            InvalidArgumentError: If the name breaks the name rules.
        """
        validate_name(recording.name)
        with self.edit() as recordings:
            existing = next((r for r in recordings if r.id == recording.id), None)
            if existing is None:
                stored = replace(recording, name=_next_free(recordings, recording.name))
                if stored.is_default:
                    recordings[:] = [replace(r, is_default=False) for r in recordings]
                recordings.append(stored)
                return stored
            others = [r for r in recordings if r.id != recording.id]
            try:
                _check_free(others, recording.name)
                name = recording.name
            except NameClashError:
                name = existing.name
            stored = replace(recording, name=name, is_default=existing.is_default)
            recordings[recordings.index(existing)] = stored
        return stored

    def rename(self, ref: str, new_name: str) -> Recording:
        """Rename a recording and return the updated entry."""
        name = validate_name(new_name)
        with self.edit() as recordings:
            target = find_in(recordings, ref)
            _check_free(recordings, name, except_id=target.id)
            updated = replace(target, name=name)
            recordings[recordings.index(target)] = updated
        return updated

    def set_default(self, ref: str) -> Recording:
        """Make one recording the default, clearing any previous default."""
        with self.edit() as recordings:
            target = find_in(recordings, ref)
            recordings[:] = [replace(r, is_default=r.id == target.id) for r in recordings]
            updated = next(r for r in recordings if r.id == target.id)
        return updated

    def remove(self, recording_id: str) -> Recording:
        """Remove an entry by exact id (clearing the default with it) and return it."""
        with self.edit() as recordings:
            matches = [r for r in recordings if r.id == recording_id]
            if not matches:
                raise NotFoundError(recording_id)
            recordings.remove(matches[0])
        return matches[0]
