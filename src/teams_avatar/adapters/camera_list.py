"""List physical cameras through Windows' device inventory (research R2, FR-002).

PyAV cannot list DirectShow devices, so the tool asks Windows for Plug and Play devices of class
Camera or Image. Virtual cameras are not PnP devices, so OBS Virtual Camera is normally absent;
it is filtered by name as well because recording from it would feed the tool's output back in.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from typing import Any

from teams_avatar.errors import DeviceError
from teams_avatar.models import CameraInfo

LIST_TIMEOUT_S = 10
EXCLUDED_NAMES = {"obs virtual camera"}
# Camera-class devices first: the Image class also holds scanners, which are rarely meant.
_CLASS_ORDER = {"Camera": 0, "Image": 1}

_QUERY = (
    "Get-CimInstance Win32_PnPEntity | "
    "Where-Object { $_.PNPClass -in @('Camera','Image') -and $_.Status -eq 'OK' } | "
    "Select-Object Name, PNPClass, Status | ConvertTo-Json -Compress"
)
COMMAND = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _QUERY]

Runner = Callable[[list[str], float], "subprocess.CompletedProcess[str]"]


def _run(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    """Run a command hidden, capturing text output."""
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def _rows(stdout: str) -> list[dict[str, Any]]:
    """Parse ConvertTo-Json output, which is one object for a single device or a list.

    Raises:
        DeviceError: If the output is not the expected JSON shape.
    """
    if not stdout.strip():
        return []
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise DeviceError("The camera list from Windows could not be read") from exc
    items = data if isinstance(data, list) else [data]
    if not all(isinstance(i, dict) and isinstance(i.get("Name"), str) for i in items):
        raise DeviceError("The camera list from Windows had an unexpected format")
    return items


class CameraLister:
    """List physical cameras by name, numbering duplicates ``Name``, ``Name #2``..."""

    def __init__(self, run: Runner = _run) -> None:
        """Use ``run`` to execute the PowerShell query (injected for tests)."""
        self._run = run

    def list(self) -> list[CameraInfo]:
        """Return the cameras in the order ``record`` uses for its default.

        Raises:
            DeviceError: If the query fails, times out or returns unexpected output.
        """
        try:
            result = self._run(COMMAND, LIST_TIMEOUT_S)
        except subprocess.TimeoutExpired as exc:
            raise DeviceError("Windows did not return the camera list within 10 seconds") from exc
        except OSError as exc:
            raise DeviceError("The camera list could not be read from Windows") from exc
        if result.returncode != 0:
            raise DeviceError("The camera list could not be read from Windows")
        rows = [r for r in _rows(result.stdout) if r["Name"].casefold() not in EXCLUDED_NAMES]
        rows.sort(key=lambda r: _CLASS_ORDER.get(str(r.get("PNPClass")), 2))
        cameras: list[CameraInfo] = []
        seen: dict[str, int] = {}
        for row in rows:
            device_name = row["Name"]
            number = seen.get(device_name, 0)
            seen[device_name] = number + 1
            shown = device_name if number == 0 else f"{device_name} #{number + 1}"
            cameras.append(CameraInfo(name=shown, device_name=device_name, device_number=number))
        return cameras
