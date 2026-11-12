from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from teams_avatar.app import App
from teams_avatar.errors import (
    AlreadyPresentingError,
    NoDefaultError,
    NotFoundError,
    RecordingUnreadableError,
    ServiceError,
    VirtualCameraError,
)
from teams_avatar.paths import DataPaths
from tests.conftest import ClipFactory
from tests.fakes import FakeClock, FakeControlChannel, FakeLauncher, FakeProcess


class FakeService:
    """Answers control requests like the camera service would."""

    def __init__(self) -> None:
        self.status: dict[str, Any] = {
            "service_running": True,
            "presenting": False,
            "recording_id": None,
            "recording_name": None,
            "elapsed_s": None,
            "canvas_width": 1280,
            "canvas_height": 720,
            "quality": "720p",
            "indicator_on": False,
        }
        self.error: str | None = None

    def __call__(self, req: dict[str, Any]) -> dict[str, Any]:
        op = req["op"]
        if op == "present":
            if self.error:
                return {"ok": False, "error": self.error, "message": "x"}
            if self.status["presenting"] and self.status["recording_id"] != req["recording_id"]:
                return {"ok": False, "error": "already_presenting", "message": "busy"}
            self.status.update(
                presenting=True,
                recording_id=req["recording_id"],
                recording_name=req["name"],
                elapsed_s=0.0,
                indicator_on=req["indicator"],
            )
        elif op == "stop":
            self.status.update(presenting=False, recording_id=None, recording_name=None)
        elif op == "indicator":
            self.status["indicator_on"] = req["on"]
        elif op == "shutdown":
            return {"ok": True}
        return {"ok": True, "status": dict(self.status)}


class Env:
    def __init__(self, data_home: Path, make_clip: ClipFactory, start_ok: bool = True) -> None:
        self.paths = DataPaths.resolve().ensure()
        self.service = FakeService()
        self.channel = FakeControlChannel(self.service)
        self.launcher = FakeLauncher(self.channel, start_ok)
        self.clock = FakeClock()
        self.app = App(
            self.paths,
            control=self.channel,
            launcher=self.launcher,
            clock=self.clock,
            sleep=self.clock.advance,
        )
        self.app.acknowledge_first_use()
        src = make_clip(data_home.parent / "src.mp4", 60, 320, 180)
        self.office = self.app.import_file(src, "Office").recording
        self.garden = self.app.import_file(src, "Garden").recording


@pytest.fixture
def env(data_home: Path, make_clip: ClipFactory) -> Env:
    return Env(data_home, make_clip)


def test_present_named_starts_service(env: Env) -> None:
    status = env.app.present("office")
    assert env.launcher.starts == 1
    assert status.presenting and status.recording_name == "Office"
    req = env.channel.requests[-1]
    assert req["op"] == "present"
    assert req["recording_id"] == env.office.id
    assert req["path"] == str(env.paths.recording_file(env.office.id))
    assert req["quality"] == "720p" and req["indicator"] is False


def test_present_default(env: Env) -> None:
    with pytest.raises(NoDefaultError):
        env.app.present(None)
    env.app.library.set_default("Garden")
    assert env.app.present(None).recording_name == "Garden"


def test_present_same_twice_is_idempotent(env: Env) -> None:
    env.app.present("Office")
    assert env.app.present("Office").recording_name == "Office"
    assert env.launcher.starts == 1


def test_present_other_refused(env: Env) -> None:
    env.app.present("Office")
    with pytest.raises(AlreadyPresentingError) as info:
        env.app.present("Garden")
    assert info.value.current_name == "Office"


def test_unknown_recording(env: Env) -> None:
    with pytest.raises(NotFoundError):
        env.app.present("nothing")


@pytest.mark.parametrize("damage", ["missing", "garbage"])
def test_unreadable_file_names_recording(env: Env, damage: str) -> None:
    f = env.paths.recording_file(env.office.id)
    if damage == "missing":
        f.unlink()
    else:
        f.write_bytes(b"garbage")
    with pytest.raises(RecordingUnreadableError, match="Office"):
        env.app.present("Office")
    assert env.launcher.starts == 0


def test_service_start_times_out_after_10s(data_home: Path, make_clip: ClipFactory) -> None:
    env = Env(data_home, make_clip, start_ok=False)
    start = env.clock()
    with pytest.raises(ServiceError):
        env.app.present("Office")
    assert env.clock() - start == pytest.approx(10, abs=0.5)


@pytest.mark.parametrize(
    ("code", "kind"),
    [("virtual_camera_not_installed", "not_installed"), ("virtual_camera_in_use", "in_use")],
)
def test_virtual_camera_errors(env: Env, code: str, kind: str) -> None:
    env.service.error = code
    with pytest.raises(VirtualCameraError) as info:
        env.app.present("Office")
    assert info.value.kind == kind


def test_service_unreadable_reply(env: Env) -> None:
    env.service.error = "unreadable"
    with pytest.raises(RecordingUnreadableError):
        env.app.present("Office")


def test_stop_and_status(env: Env) -> None:
    not_running = env.app.status()
    assert not_running.service_running is False and not not_running.presenting
    assert env.app.stop().presenting is False  # no-op when not running
    env.app.present("Office")
    assert env.app.status().recording_name == "Office"
    assert env.app.stop().presenting is False


def test_set_indicator_saves_and_forwards(env: Env) -> None:
    assert env.app.set_indicator(True).indicator_on is True
    assert env.channel.requests == []  # service not running: only saved
    env.app.present("Office")
    assert env.channel.requests[-1]["indicator"] is True
    env.app.set_indicator(False)
    assert env.channel.requests[-1] == {"op": "indicator", "on": False}
    assert env.app.settings.load().indicator_on is False


def test_set_quality(env: Env) -> None:
    result = env.app.set_quality("1080p")
    assert result.settings.presentation_quality == "1080p"
    assert result.applies_at_next_service_start is False
    env.app.present("Office")
    assert env.app.set_quality("720p").applies_at_next_service_start is True


def test_shutdown(env: Env) -> None:
    env.app.shutdown()  # no-op when not running
    env.app.present("Office")
    inner = env.service

    def exits_on_shutdown(req: dict[str, Any]) -> dict[str, Any]:
        if req["op"] == "shutdown":
            env.channel.running = False  # the service is gone right after its ack
        return inner(req)

    env.channel.handler = exits_on_shutdown
    env.app.shutdown()
    assert {"op": "shutdown"} in env.channel.requests


def test_start_budget_holds_against_silent_service(data_home: Path, make_clip: ClipFactory) -> None:
    env = Env(data_home, make_clip)
    env.channel.hang_s = 6.0
    env.channel.clock = env.clock
    env.launcher.channel = env.channel
    start = env.clock()
    with pytest.raises(ServiceError):
        env.app.present("Office")
    assert env.clock() - start <= 10.5
    assert all(t is None or t <= 10 for t in env.channel.timeouts)


def test_service_died_names_exit_code_and_log(data_home: Path, make_clip: ClipFactory) -> None:
    env = Env(data_home, make_clip)
    env.launcher.exit_code = 1
    with pytest.raises(ServiceError, match=r"exit code 1") as info:
        env.app.present("Office")
    assert str(env.paths.log_file) in info.value.message
    assert env.clock() - 1000.0 < 1


def test_race_loser_exit_zero_uses_winner(data_home: Path, make_clip: ClipFactory) -> None:
    env = Env(data_home, make_clip)
    env.launcher.exit_code = 0
    env.channel.running = False

    original = env.launcher.start

    def start() -> FakeProcess:
        proc = original()
        env.channel.running = True  # the winning instance already serves
        return proc

    env.launcher.start = start  # type: ignore[method-assign]
    assert env.app.present("Office").presenting


@pytest.mark.parametrize(("installed", "kind"), [(True, "in_use"), (False, "not_installed")])
def test_service_exit_4_gives_virtual_camera_error(
    env: Env, monkeypatch: pytest.MonkeyPatch, installed: bool, kind: str
) -> None:
    monkeypatch.setattr("teams_avatar.adapters.virtual_camera.obs_installed", lambda: installed)
    env.launcher.exit_code = 4
    with pytest.raises(VirtualCameraError) as info:
        env.app.present("Office")
    assert info.value.kind == kind


@pytest.mark.parametrize("reply", [{"ok": True}, {"ok": True, "status": "x"}])
def test_malformed_ok_reply_is_service_error(env: Env, reply: dict[str, Any]) -> None:
    env.channel.handler = lambda req: reply
    with pytest.raises(ServiceError, match="does not understand"):
        env.app.present("Office")


def test_unknown_error_keeps_service_message(env: Env) -> None:
    env.channel.handler = lambda req: {"ok": False, "error": "internal", "message": "boom happened"}
    with pytest.raises(ServiceError, match="boom happened"):
        env.app.present("Office")
    env.channel.handler = lambda req: {"ok": False, "error": "shutting_down", "message": "x"}
    with pytest.raises(ServiceError, match="shutting down.*few seconds"):
        env.app.present("Office")


def test_status_survives_hung_service_and_quality_applies(env: Env) -> None:
    env.channel.running = True
    env.channel.hang_s = 1.0
    env.channel.clock = env.clock
    status = env.app.status()
    assert status.service_running is False and status.last_failure
    assert env.app.set_quality("1080p").applies_at_next_service_start is True


def test_already_presenting_without_name_is_unquoted(env: Env) -> None:
    env.app.present("Office")
    env.service.status["recording_name"] = None
    with pytest.raises(AlreadyPresentingError) as info:
        env.app.present("Garden")
    assert info.value.message.startswith("Another recording is already")


def test_present_uses_a_reply_budget_longer_than_the_default(env: Env) -> None:
    """The service may spend up to ~10 s opening the file; the CLI must not give up at 5 s."""
    env.app.present("Office")
    present_timeouts = [
        t
        for req, t in zip(env.channel.requests, env.channel.timeouts, strict=True)
        if req["op"] == "present"
    ]
    assert present_timeouts == [15.0]


def _slow_exit(env: Env, delay_s: float) -> None:
    """Make the service ack ``shutdown`` at once but stay reachable for ``delay_s`` more."""
    inner = env.service
    state: dict[str, float] = {}

    def handler(req: dict[str, Any]) -> dict[str, Any]:
        if req["op"] == "shutdown":
            state["until"] = env.clock() + delay_s
            return {"ok": True}
        if "until" in state and env.clock() >= state["until"]:
            env.channel.running = False
        return inner(req)

    env.channel.handler = handler


def test_shutdown_waits_until_the_service_is_gone(env: Env) -> None:
    env.app.present("Office")
    _slow_exit(env, 0.6)
    start = env.clock()
    env.app.shutdown()
    assert env.channel.running is False
    assert env.clock() - start >= 0.6


def test_shutdown_fails_plainly_when_the_service_never_exits(env: Env) -> None:
    env.app.present("Office")
    env.channel.handler = lambda req: {"ok": True, "status": {"service_running": True}}
    start = env.clock()
    with pytest.raises(ServiceError) as info:
        env.app.shutdown()
    assert 4.5 <= env.clock() - start <= 6.0
    assert "did not stop" in info.value.message


def _exits_at_once(env: Env) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Return a handler that acks shutdown and closes the pipe (the lock may still be held)."""

    def handler(req: dict[str, Any]) -> dict[str, Any]:
        env.channel.running = False
        return {"ok": True}

    return handler


def test_shutdown_waits_until_the_service_releases_its_lock(env: Env) -> None:
    """A following present must not lose the single-instance race to the old service.

    The service closes its pipe a moment before its process exits and releases the lock.
    """
    from teams_avatar.locks import ServiceLock

    env.app.present("Office")
    old_service = ServiceLock(env.paths.service_lock)
    assert old_service.try_acquire()
    env.channel.handler = _exits_at_once(env)
    start = env.clock()

    def sleep(seconds: float) -> None:
        env.clock.advance(seconds)
        if env.clock() - start >= 0.5:
            old_service.release()

    env.app._sleep = sleep
    try:
        env.app.shutdown()
    finally:
        old_service.release()
    assert env.clock() - start >= 0.5


def test_shutdown_fails_plainly_when_the_lock_is_never_released(env: Env) -> None:
    from teams_avatar.locks import ServiceLock

    env.app.present("Office")
    old_service = ServiceLock(env.paths.service_lock)
    assert old_service.try_acquire()
    env.channel.handler = _exits_at_once(env)
    try:
        with pytest.raises(ServiceError, match="did not stop"):
            env.app.shutdown()
    finally:
        old_service.release()


@pytest.mark.parametrize("code", ["start_timeout", "busy"])
def test_slow_service_is_not_reported_as_unreadable(env: Env, code: str) -> None:
    env.service.error = code
    with pytest.raises(ServiceError) as info:
        env.app.present("Office")
    assert not isinstance(info.value, RecordingUnreadableError)
    assert "delete" not in info.value.message.lower()
    assert "try again" in info.value.message.lower()
