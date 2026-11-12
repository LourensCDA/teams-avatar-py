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
        """Start the service process."""
        ...


class ProcessLauncher:
    """Launches ``python -m teams_avatar service`` in a new minimised console window."""

    def __init__(self, paths: DataPaths | None = None) -> None:
        """Remember the data folder to hand to the service through the environment."""
        self._paths = paths

    def start(self) -> None:
        """Spawn the service and return at once.

        A new console keeps the service alive when the launching terminal closes and gives it
        its own window (research R4); the window is minimised without taking focus.

        Raises:
            ServiceError: If the process cannot be created.
        """
        info = subprocess.STARTUPINFO()
        info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        info.wShowWindow = SW_SHOWMINNOACTIVE
        env = dict(os.environ)
        if self._paths is not None:
            env["TEAMS_AVATAR_HOME"] = str(self._paths.root)
        try:
            subprocess.Popen(
                [sys.executable, "-m", "teams_avatar", "service"],
                creationflags=subprocess.CREATE_NEW_CONSOLE,
                startupinfo=info,
                env=env,
                close_fds=True,
            )
        except OSError as exc:
            raise ServiceError(
                f"The camera service could not be started: {exc.strerror or exc}"
            ) from exc
