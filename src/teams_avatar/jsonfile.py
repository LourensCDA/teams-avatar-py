"""Atomic, versioned JSON files (research R8): temp file + ``os.replace``, ``schema_version: 1``."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from teams_avatar.errors import DataFileError

SCHEMA_VERSION = 1


def read_versioned(path: Path) -> dict[str, Any] | None:
    """Read a JSON object with ``schema_version`` 1; return None if the file does not exist.

    Raises:
        DataFileError: If the file is not valid JSON, not an object, or has another version.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise DataFileError(path.name, f"it cannot be read ({exc.strerror})") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DataFileError(path.name, "it is not valid JSON") from exc
    if not isinstance(data, dict):
        raise DataFileError(path.name, "it does not hold a JSON object")
    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        raise DataFileError(path.name, f"its schema version {version!r} is not supported")
    return data


def write_atomic(path: Path, data: dict[str, Any]) -> None:
    """Write ``data`` plus ``schema_version`` so readers never see a half-written file."""
    payload = {"schema_version": SCHEMA_VERSION, **data}
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
