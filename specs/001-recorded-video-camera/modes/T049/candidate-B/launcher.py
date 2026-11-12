"""Starts the camera service as a detached background process (research R4/R11)."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Protocol

from teams_avatar.errors import ServiceError

SW_SHOWMINNOACTIVE = 7


class ServiceLauncher(Protocol):
    """Anything that can start the camera service process."""

    def start(self) -> None:
        """Start the service without waiting for it to be ready."""
        ...


class SubprocessLauncher:
    """Runs ``python -m teams_avatar service`` in its own minimised console window."""

    def __init__(self, home: str | None = None) -> None:
        """Remember the data folder to pass on as ``TEAMS_AVATAR_HOME``."""
        self._home = home

    def start(self) -> None:
        """Spawn the service and return at once.

        A new console keeps the service alive when the launching terminal closes, and the
        minimised, non-activated window avoids stealing focus from the meeting.

        Raises:
            ServiceError: If the process cannot be created.
        """
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = SW_SHOWMINNOACTIVE
        env = dict(os.environ)
        if self._home:
            env["TEAMS_AVATAR_HOME"] = self._home
        try:
            subprocess.Popen(
                [sys.executable, "-m", "teams_avatar", "service"],
                creationflags=subprocess.CREATE_NEW_CONSOLE,
                startupinfo=startup,
                env=env,
                close_fds=True,
            )
        except OSError as exc:
            raise ServiceError(f"The camera service could not be started: {exc}") from exc
