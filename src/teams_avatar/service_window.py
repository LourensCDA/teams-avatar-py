"""The camera service's console window: always-visible state in its title (research R11)."""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable
from datetime import datetime

FIRST_LINE = "Keep this window open during meetings; closing it stops the camera."
IDLE_TITLE = "Teams Avatar — IDLE — camera paused"


def presenting_title(name: str, indicator_on: bool) -> str:
    """Return the title shown while ``name`` is presented."""
    state = "ON" if indicator_on else "OFF"
    return f'Teams Avatar — PRESENTING "{name}" — indicator {state}'


def _set_console_title(title: str) -> None:
    """Set the console title through Win32 so it shows on the taskbar button."""
    ctypes.windll.kernel32.SetConsoleTitleW(title)


def _print_line(line: str) -> None:
    """Print one line to the console and flush so it appears at once."""
    print(line, file=sys.stdout, flush=True)


def _clock() -> str:
    """Return the local time as ``HH:MM:SS``."""
    return datetime.now().strftime("%H:%M:%S")


class ServiceWindow:
    """Writes the service state to the console title and one timestamped line per change.

    The title and output functions are injected so tests run without a real console.
    """

    def __init__(
        self,
        set_title: Callable[[str], object] = _set_console_title,
        write_line: Callable[[str], object] = _print_line,
        now: Callable[[], str] = _clock,
    ) -> None:
        """Bind the window to its title setter, line writer and clock."""
        self._set_title = set_title
        self._write = write_line
        self._now = now

    def started(self) -> None:
        """Show the keep-open instruction and the idle title."""
        self._write(FIRST_LINE)
        self._set_title(IDLE_TITLE)

    def presenting(self, name: str, indicator_on: bool) -> None:
        """Show that ``name`` is being presented and whether the indicator is on."""
        self._set_title(presenting_title(name, indicator_on))
        state = "ON" if indicator_on else "OFF"
        self._write(f'{self._now()} Presenting "{name}". Recording indicator: {state}.')

    def idle(self, line: str) -> None:
        """Show the idle title and a state line (e.g. ``Presentation stopped.``)."""
        self._set_title(IDLE_TITLE)
        self._write(f"{self._now()} {line}")

    def failed(self, name: str, reason: str) -> None:
        """Report a failed presentation in plain language (FR-014) and show the idle title."""
        self._set_title(IDLE_TITLE)
        self._write(
            f'{self._now()} Presentation of "{name}" failed: {reason.rstrip(".")}. '
            'The camera now shows "Camera paused".'
        )

    def note(self, line: str) -> None:
        """Write any other timestamped line without changing the title."""
        self._write(f"{self._now()} {line}")
