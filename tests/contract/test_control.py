"""Contract tests for the named-pipe control channel (no hardware needed)."""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Iterator
from multiprocessing.connection import Client, Listener
from pathlib import Path
from typing import Any

import pytest

from teams_avatar.control import ControlClient, ControlServer, pipe_name, read_key, write_key
from teams_avatar.errors import ServiceError, ServiceNotRunning
from teams_avatar.paths import DataPaths

KEY = b"k" * 32


def address() -> str:
    return pipe_name(suffix=f"test-{uuid.uuid4().hex}")


class Recorder:
    def __init__(self) -> None:
        self.seen: list[dict[str, Any]] = []

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        self.seen.append(request)
        return {"ok": True, "status": {"echo": request["op"]}}


@pytest.fixture
def served() -> Iterator[tuple[str, Recorder]]:
    addr = address()
    handler = Recorder()
    server = ControlServer(addr, KEY)
    thread = threading.Thread(target=server.serve, args=(handler,), daemon=True)
    thread.start()
    yield addr, handler
    server.stop()
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_pipe_name_is_per_user() -> None:
    name = pipe_name()
    assert name.startswith("\\\\.\\pipe\\teams-avatar-")
    assert pipe_name(suffix="x").endswith("-x")


def test_key_file_round_trip(data_home: Path) -> None:
    paths = DataPaths.resolve().ensure()
    key = write_key(paths)
    assert len(key) >= 32
    assert read_key(paths) == key
    assert write_key(paths) != key  # regenerated on every service start


@pytest.mark.parametrize(
    "request_",
    [
        {
            "op": "present",
            "recording_id": "a" * 32,
            "path": "C:/x.mp4",
            "quality": "720p",
            "indicator": False,
        },
        {"op": "stop"},
        {"op": "status"},
        {"op": "indicator", "on": True},
        {"op": "shutdown"},
    ],
)
def test_round_trip(served: tuple[str, Recorder], request_: dict[str, Any]) -> None:
    addr, handler = served
    reply = ControlClient(addr, KEY).request(request_)
    assert reply == {"ok": True, "status": {"echo": request_["op"]}}
    assert handler.seen == [request_]


@pytest.mark.parametrize(
    "bad",
    [
        {"op": "launch_missiles"},
        {"op": "indicator", "on": "yes"},
        {"op": "present", "recording_id": 5},
        {"nope": 1},
    ],
)
def test_invalid_requests_rejected(served: tuple[str, Recorder], bad: dict[str, Any]) -> None:
    addr, handler = served
    reply = ControlClient(addr, KEY).request(bad)
    assert reply["ok"] is False and reply["error"] == "bad_request"
    assert handler.seen == []


def test_wrong_key_rejected(served: tuple[str, Recorder]) -> None:
    addr, handler = served
    with pytest.raises(ServiceError):
        ControlClient(addr, b"w" * 32).request({"op": "status"})
    assert handler.seen == []


def test_non_json_payload_rejected(served: tuple[str, Recorder]) -> None:
    addr, handler = served
    conn = Client(addr, family="AF_PIPE", authkey=KEY)
    try:
        conn.send({"op": "status"})  # a pickle, which the service must never unpickle
        raw = conn.recv_bytes()
    finally:
        conn.close()
    assert b"bad_request" in raw
    assert handler.seen == []


def test_no_server_raises_within_1s() -> None:
    start = time.monotonic()
    with pytest.raises(ServiceNotRunning):
        ControlClient(address(), KEY).request({"op": "status"})
    assert time.monotonic() - start < 1.0


def test_mute_server_times_out() -> None:
    addr = address()
    listener = Listener(addr, family="AF_PIPE")  # exists but never accepts or answers
    try:
        start = time.monotonic()
        with pytest.raises(ServiceError):
            ControlClient(addr, KEY).request({"op": "status"})
        assert time.monotonic() - start < 6.5
    finally:
        listener.close()


def test_busy_pipe_raises_not_running_within_budget() -> None:
    addr = address()
    listener = Listener(addr, family="AF_PIPE")
    squatter = Client(addr, family="AF_PIPE")  # takes the only instance
    try:
        start = time.monotonic()
        with pytest.raises(ServiceNotRunning):
            ControlClient(addr, KEY).request({"op": "status"})
        assert time.monotonic() - start < 1.5
    finally:
        squatter.close()
        listener.close()


def test_explicit_timeout_extends_the_reply_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """A caller that asks for a longer total (e.g. ``present``) must get it, not the 5 s cap."""
    import teams_avatar.control as control_module

    monkeypatch.setattr(control_module, "REPLY_TIMEOUT_S", 0.3)
    addr = address()

    def slow(request: dict[str, Any]) -> dict[str, Any]:
        time.sleep(0.8)
        return {"ok": True, "status": {}}

    server = ControlServer(addr, KEY)
    thread = threading.Thread(target=server.serve, args=(slow,), daemon=True)
    thread.start()
    try:
        with pytest.raises(ServiceError):
            ControlClient(addr, KEY).request({"op": "status"})
        assert ControlClient(addr, KEY).request({"op": "status"}, timeout_s=5.0)["ok"] is True
    finally:
        server.stop()
        thread.join(timeout=3)
