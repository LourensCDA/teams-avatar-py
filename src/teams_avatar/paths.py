"""Resolve where the tool keeps its per-user data (research R8)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

HOME_ENV = "TEAMS_AVATAR_HOME"


@dataclass(frozen=True)
class DataPaths:
    """Locations of every file the tool reads or writes, under one root folder."""

    root: Path

    @classmethod
    def resolve(cls) -> DataPaths:
        """Return paths under ``TEAMS_AVATAR_HOME`` or the ``TeamsAvatar`` folder in LOCALAPPDATA.

        Raises:
            RuntimeError: If neither variable is set (not a normal Windows session).
        """
        override = os.environ.get(HOME_ENV)
        if override:
            return cls(Path(override))
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            raise RuntimeError("LOCALAPPDATA is not set; set TEAMS_AVATAR_HOME instead")
        return cls(Path(local) / "TeamsAvatar")

    def ensure(self) -> DataPaths:
        """Create the root, recordings and logs folders if missing; return self."""
        self.recordings.mkdir(parents=True, exist_ok=True)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        return self

    @property
    def library(self) -> Path:
        """Return the library path."""
        return self.root / "library.json"

    @property
    def settings(self) -> Path:
        """Return the settings path."""
        return self.root / "settings.json"

    @property
    def activity(self) -> Path:
        """Return the activity path."""
        return self.root / "activity.jsonl"

    @property
    def recordings(self) -> Path:
        """Return the recordings path."""
        return self.root / "recordings"

    @property
    def log_file(self) -> Path:
        """Return the log file path."""
        return self.root / "logs" / "teams-avatar.log"

    @property
    def service_key(self) -> Path:
        """Return the service key path."""
        return self.root / "service.key"

    @property
    def service_lock(self) -> Path:
        """Return the service lock path."""
        return self.root / "service.lock"

    @property
    def data_lock(self) -> Path:
        """Return the data lock path."""
        return self.root / "data.lock"

    def recording_file(self, recording_id: str) -> Path:
        """Return the finished video file for a recording id."""
        return self.recordings / f"{recording_id}.mp4"

    def partial_file(self, recording_id: str) -> Path:
        """Return the in-progress video file, renamed to ``recording_file`` on success."""
        return self.recordings / f"{recording_id}.partial.mp4"
