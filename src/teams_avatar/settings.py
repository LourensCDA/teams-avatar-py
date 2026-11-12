"""Per-user settings in ``settings.json`` (FR-017, FR-023)."""

from __future__ import annotations

from dataclasses import asdict, replace

from teams_avatar.errors import DataFileError
from teams_avatar.jsonfile import read_versioned, write_atomic
from teams_avatar.locks import DataLock
from teams_avatar.models import QUALITIES, Settings
from teams_avatar.paths import DataPaths

_BOOL_FIELDS = ("indicator_on", "first_use_acknowledged")


def _validate(values: dict[str, object]) -> None:
    """Raise ValueError if any known setting has an invalid value."""
    for key in _BOOL_FIELDS:
        if key in values and not isinstance(values[key], bool):
            raise ValueError(f"{key} must be true or false")
    if "presentation_quality" in values and values["presentation_quality"] not in QUALITIES:
        raise ValueError("presentation_quality must be 720p or 1080p")


class SettingsStore:
    """Load and update ``settings.json``; missing settings take their defaults."""

    def __init__(self, paths: DataPaths) -> None:
        """Bind the store to a data folder."""
        self.paths = paths

    def load(self) -> Settings:
        """Return the settings, or defaults if the file does not exist.

        Raises:
            DataFileError: If the file is unreadable or holds invalid values.
        """
        data = read_versioned(self.paths.settings)
        if data is None:
            return Settings()
        known = {k: v for k, v in data.items() if k in Settings.__dataclass_fields__}
        try:
            _validate(known)
        except ValueError as exc:
            raise DataFileError(self.paths.settings.name, str(exc)) from exc
        return Settings(**known)

    def update(self, **changes: object) -> Settings:
        """Apply ``changes`` and save; return the new settings.

        Raises:
            ValueError: If a change names an unknown setting or has an invalid value.
        """
        unknown = set(changes) - set(Settings.__dataclass_fields__)
        if unknown:
            raise ValueError(f"unknown settings: {sorted(unknown)}")
        _validate(changes)
        with DataLock(self.paths.data_lock):
            updated = replace(self.load(), **changes)  # type: ignore[arg-type]  # validated above
            write_atomic(self.paths.settings, asdict(updated))
        return updated
