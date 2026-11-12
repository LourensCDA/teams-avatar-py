"""Structured JSON logs with a session correlation id (constitution Principle IV)."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime

from teams_avatar.paths import DataPaths

session_id_var: ContextVar[str | None] = ContextVar("session_id", default=None)

# Attributes every LogRecord has; anything else came from ``extra=``.
_STANDARD = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime"}


@contextmanager
def session(session_id: str) -> Iterator[None]:
    """Tag every log line written inside the block with ``session_id``."""
    token = session_id_var.set(session_id)
    try:
        yield
    finally:
        session_id_var.reset(token)


def _is_picture_like(value: object) -> bool:
    """Return True for bytes and array-like values, which could carry frame content."""
    return isinstance(value, bytes | bytearray | memoryview) or hasattr(value, "__array__")


class JsonFormatter(logging.Formatter):
    """Format records as one JSON object per line: ``ts``, ``level``, ``msg``, ``session_id``."""

    def format(self, record: logging.LogRecord) -> str:
        """Return the JSON line for ``record``.

        Raises:
            ValueError: If an ``extra`` field holds bytes or an array (possible picture data),
                so nothing is written rather than leaking it.
        """
        data: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "session_id": session_id_var.get(),
        }
        for key, value in vars(record).items():
            if key in _STANDARD or key.startswith("_"):
                continue
            if _is_picture_like(value):
                raise ValueError(f"log field {key!r} looks like picture data and is not logged")
            data[key] = value
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, default=str, ensure_ascii=False)


def configure(paths: DataPaths, level: int = logging.INFO) -> None:
    """Send the ``teams_avatar`` logger to ``logs/teams-avatar.log`` as JSON lines."""
    paths.log_file.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("teams_avatar")
    if any(getattr(h, "_teams_avatar", False) for h in logger.handlers):
        return
    handler = logging.FileHandler(paths.log_file, encoding="utf-8")
    handler.setFormatter(JsonFormatter())
    handler._teams_avatar = True  # type: ignore[attr-defined]  # marker so configure() is idempotent
    logger.addHandler(handler)
    logger.setLevel(level)
