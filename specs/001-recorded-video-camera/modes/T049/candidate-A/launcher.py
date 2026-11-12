"""Starts the camera service as a detached process with its own minimised console (R4/R11)."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Protocol

from teams_avatar.errors import ServiceError
from teams_avatar.paths import DataPaths

SW_SHOWMINNOACTIVE = 7


class ServiceLauncher(Protocol):
    """Anything that can start the camera service process without waiting for it."""

    def start(self) -> None:
        """Start the service; readiness is checked by the caller."""
        ...


class SubprocessLauncher:
    """Launches ``python -m teams_avatar service`` in a new, minimised console window."""

    def __init__(self, paths: DataPaths) -> None:
        """Remember the data folder so the service uses the same one as this process."""
        self._paths = paths

    def start(self) -> None:
        """Spawn the service and return immediately.

        A new console keeps the service alive when the launching terminal closes and gives it
        the window whose title shows the indicator state; showing it minimised and inactive
        avoids stealing focus from a meeting.

        Raises:
            ServiceError: If the process cannot be created.
        """
        info = subprocess.STARTUPINFO()
        info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        info.wShowWindow = SW_SHOWMINNOACTIVE
        env = {**os.environ, "TEAMS_AVATAR_HOME": str(self._paths.root)}
        try:
            subprocess.Popen(
                [sys.executable, "-m", "teams_avatar", "service"],
                creationflags=subprocess.CREATE_NEW_CONSOLE,
                startupinfo=info,
                env=env,
                close_fds=True,
            )
        except OSError as exc:
            raise ServiceError(f"The camera service could not be started ({exc})") from exc
