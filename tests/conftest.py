"""Shared fixtures: isolated data folder, no-network guard, hardware gating, synthetic clips."""

from __future__ import annotations

import os
import socket
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from tests.clips import write_clip

HW_SKIP_REASON = "needs camera/OBS; set TEAMS_AVATAR_HW=1"

ClipFactory = Callable[..., Path]


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip hardware-marked tests unless TEAMS_AVATAR_HW=1, with a recorded reason."""
    if os.environ.get("TEAMS_AVATAR_HW") == "1":
        return
    skip = pytest.mark.skip(reason=HW_SKIP_REASON)
    for item in items:
        if "hardware" in item.keywords:
            item.add_marker(skip)


class _NoNetworkSocket(socket.socket):
    """Socket replacement that refuses to be created (constitution Principle I)."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise RuntimeError("network access is not allowed in unit and integration tests")


@pytest.fixture(autouse=True)
def _no_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``socket.socket`` raise in unit and integration tests."""
    parts = Path(str(request.node.path)).parts
    if "unit" in parts or "integration" in parts:
        monkeypatch.setattr(socket, "socket", _NoNetworkSocket)


@pytest.fixture
def data_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point ``TEAMS_AVATAR_HOME`` at a fresh temporary folder."""
    home = tmp_path / "home"
    monkeypatch.setenv("TEAMS_AVATAR_HOME", str(home))
    yield home


@pytest.fixture
def make_clip() -> ClipFactory:
    """Return a factory ``make_clip(path, frames, width, height, fps=30, distinct=True)``."""
    return write_clip
