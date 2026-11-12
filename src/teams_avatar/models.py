"""Plain data objects shared by the facade, storage and front ends (data-model.md)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, get_args

Origin = Literal["recorded", "imported"]
RecordingWarning = Literal["too_short", "loop_ghosting"]
Quality = Literal["720p", "1080p"]
SessionOutcome = Literal["saved", "cancelled", "failed"]
EndReason = Literal["stopped", "failed", "service_shutdown"]

ORIGINS: tuple[str, ...] = get_args(Origin)
WARNINGS: tuple[str, ...] = get_args(RecordingWarning)
QUALITIES: tuple[str, ...] = get_args(Quality)

CANVAS_SIZES: dict[str, tuple[int, int]] = {"720p": (1280, 720), "1080p": (1920, 1080)}


def _parse_time(value: object, field_name: str) -> datetime:
    """Parse an ISO 8601 timestamp that must carry a time zone."""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a timestamp string")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a time zone")
    return parsed


@dataclass(frozen=True)
class Recording:
    """A stored recording: one library entry plus its video file."""

    id: str
    name: str
    origin: Origin
    created_at: datetime
    duration_s: float
    frame_count: int
    width: int
    height: int
    size_bytes: int
    is_default: bool = False
    warnings: list[RecordingWarning] = field(default_factory=list)
    source_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON form stored in ``library.json``."""
        return {
            "id": self.id,
            "name": self.name,
            "origin": self.origin,
            "created_at": self.created_at.isoformat(),
            "duration_s": self.duration_s,
            "frame_count": self.frame_count,
            "width": self.width,
            "height": self.height,
            "size_bytes": self.size_bytes,
            "is_default": self.is_default,
            "warnings": list(self.warnings),
            "source_name": self.source_name,
        }

    @classmethod
    def from_dict(cls, data: object) -> Recording:
        """Build a recording from its JSON form, validating every field.

        Raises:
            ValueError: If any field is missing or has the wrong type or value.
        """
        if not isinstance(data, dict):
            raise ValueError("a recording entry must be an object")
        try:
            rid, name, origin = data["id"], data["name"], data["origin"]
            numbers = {k: data[k] for k in ("frame_count", "width", "height", "size_bytes")}
            duration = data["duration_s"]
            is_default = data["is_default"]
            warnings = data["warnings"]
            source_name = data.get("source_name")
            created_at = _parse_time(data["created_at"], "created_at")
        except KeyError as exc:
            raise ValueError(f"missing field {exc.args[0]}") from exc
        if not (
            isinstance(rid, str) and len(rid) == 32 and all(c in "0123456789abcdef" for c in rid)
        ):
            raise ValueError("id must be 32 hex characters")
        if not isinstance(name, str) or not name:
            raise ValueError("name must be a non-empty string")
        if origin not in ORIGINS:
            raise ValueError(f"unknown origin {origin!r}")
        for key, value in numbers.items():
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{key} must be a non-negative integer")
        if not isinstance(duration, int | float) or isinstance(duration, bool) or duration <= 0:
            raise ValueError("duration_s must be a positive number")
        if not isinstance(is_default, bool):
            raise ValueError("is_default must be true or false")
        if not isinstance(warnings, list) or any(w not in WARNINGS for w in warnings):
            raise ValueError("warnings must be a list of known warnings")
        if source_name is not None and not isinstance(source_name, str):
            raise ValueError("source_name must be a string or null")
        return cls(
            id=rid,
            name=name,
            origin=origin,
            created_at=created_at,
            duration_s=float(duration),
            frame_count=numbers["frame_count"],
            width=numbers["width"],
            height=numbers["height"],
            size_bytes=numbers["size_bytes"],
            is_default=is_default,
            warnings=list(warnings),
            source_name=source_name,
        )


@dataclass(frozen=True)
class Settings:
    """Per-user settings stored in ``settings.json``."""

    indicator_on: bool = False
    presentation_quality: Quality = "720p"
    first_use_acknowledged: bool = False


@dataclass(frozen=True)
class ActivityRecord:
    """One line of ``activity.jsonl`` (FR-016); never holds picture content."""

    at: datetime
    event: str
    recording_id: str | None
    recording_name: str | None
    session_id: str | None
    outcome: str
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON form of the record."""
        return {
            "at": self.at.isoformat(),
            "event": self.event,
            "recording_id": self.recording_id,
            "recording_name": self.recording_name,
            "session_id": self.session_id,
            "outcome": self.outcome,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActivityRecord:
        """Build a record from its JSON form."""
        return cls(
            at=_parse_time(data["at"], "at"),
            event=str(data["event"]),
            recording_id=data.get("recording_id"),
            recording_name=data.get("recording_name"),
            session_id=data.get("session_id"),
            outcome=str(data["outcome"]),
            detail=data.get("detail"),
        )


@dataclass(frozen=True)
class RecordingSessionInfo:
    """Outcome of one attempt to record from a physical camera."""

    session_id: str
    camera_name: str
    started_at: datetime
    outcome: SessionOutcome
    failure_reason: str | None = None


@dataclass(frozen=True)
class PresentationStatus:
    """What the camera service is doing, as reported to front ends (FR-009)."""

    service_running: bool
    presenting: bool = False
    recording_id: str | None = None
    recording_name: str | None = None
    elapsed_s: float | None = None
    canvas_width: int | None = None
    canvas_height: int | None = None
    quality: Quality = "720p"
    indicator_on: bool = False
    recording_width: int | None = None
    recording_height: int | None = None
    warnings: list[RecordingWarning] = field(default_factory=list)
    last_failure: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON form used on the control pipe and by ``--json``."""
        return {
            "service_running": self.service_running,
            "presenting": self.presenting,
            "recording_id": self.recording_id,
            "recording_name": self.recording_name,
            "elapsed_s": self.elapsed_s,
            "canvas_width": self.canvas_width,
            "canvas_height": self.canvas_height,
            "quality": self.quality,
            "indicator_on": self.indicator_on,
            "recording_width": self.recording_width,
            "recording_height": self.recording_height,
            "warnings": list(self.warnings),
            "last_failure": self.last_failure,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PresentationStatus:
        """Build a status from its JSON form."""
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})


@dataclass(frozen=True)
class CameraInfo:
    """A physical camera as listed for the user (FR-002)."""

    name: str
    device_name: str
    device_number: int = 0


@dataclass(frozen=True)
class RecordResult:
    """A recording that was just saved or imported, with its warnings."""

    recording: Recording
    warnings: list[RecordingWarning]


@dataclass(frozen=True)
class RecordingView:
    """A library entry as shown by ``list``: the recording plus live flags."""

    recording: Recording
    presenting: bool
    missing: bool
