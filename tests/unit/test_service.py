"""Camera service state machine with fakes: no OBS, files or real clock (FR-006-FR-011)."""

from __future__ import annotations

import ast
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import teams_avatar.service as service_module
from teams_avatar.activity import ActivityLog
from teams_avatar.frames import draw_indicator, indicator_region, placeholder
from teams_avatar.models import ActivityRecord
from teams_avatar.paths import DataPaths
from teams_avatar.service import CameraService
from tests.fakes import FakeClock, FakeFrameSink, FakeVideoReader

W, H = 320, 180


def wait_until(cond: Callable[[], bool], timeout: float = 3.0) -> None:
    end = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > end:
            raise AssertionError("condition not reached")
        time.sleep(0.002)


class Env:
    def __init__(self, data_home: Path, reader_factory: Callable[[], FakeVideoReader]) -> None:
        self.paths = DataPaths.resolve().ensure()
        self.sink = FakeFrameSink()
        self.clock = FakeClock()
        self.readers: list[FakeVideoReader] = []
        self.activity = ActivityLog(self.paths)

        def open_reader(path: Path) -> FakeVideoReader:
            r = reader_factory()
            self.readers.append(r)
            return r

        self.service = CameraService(
            sink=self.sink,
            canvas=(W, H),
            quality="720p",
            indicator_on=False,
            open_reader=open_reader,
            activity=self.activity,
            clock=self.clock,
        )
        self.service.open()

    def present(self, rid: str = "a" * 32, name: str = "Office") -> dict[str, Any]:
        return self.service.handle(
            {
                "op": "present",
                "recording_id": rid,
                "name": name,
                "path": f"C:/{rid}.mp4",
                "quality": "720p",
                "indicator": False,
            }
        )

    def tick(self, n: int = 1, dt: float = 1 / 30) -> list[np.ndarray]:
        start = len(self.sink.frames)
        for _ in range(n):
            self.clock.advance(dt)
            self.service.tick()
        return self.sink.frames[start:]

    def tick_when_ready(self, n: int) -> list[np.ndarray]:
        out = []
        for _ in range(n):
            wait_until(lambda: self.service.queued > 0 or not self.service.presenting)
            out += self.tick()
        return out


def is_placeholder(frame: np.ndarray) -> bool:
    return bool(np.array_equal(frame, placeholder(W, H)))


@pytest.fixture
def env(data_home: Path) -> Env:
    return Env(data_home, lambda: FakeVideoReader(200, W, H))


def test_opens_sink_at_canvas_and_idles_on_placeholder(env: Env) -> None:
    assert env.sink.size == (W, H, 30)
    frames = env.tick(3)
    assert all(is_placeholder(f) for f in frames)
    assert env.service.status()["presenting"] is False


def test_present_switches_to_recording_frames(env: Env) -> None:
    reply = env.present()
    assert reply["ok"] is True
    assert reply["status"]["presenting"] is True
    frames = env.tick_when_ready(5)
    assert [int(f[0, 0, 0]) for f in frames] == [0, 1, 2, 3, 4]


def test_present_same_again_returns_status(env: Env) -> None:
    env.present()
    again = env.present()
    assert again["ok"] is True and again["status"]["recording_name"] == "Office"
    assert len(env.readers) == 1


def test_present_other_refused(env: Env) -> None:
    env.present()
    other = env.present(rid="b" * 32, name="Garden")
    assert other == {"ok": False, "error": "already_presenting", "message": other["message"]}
    assert "Office" in other["message"]


def test_stop_shows_placeholder_on_next_frame(env: Env) -> None:
    env.present()
    env.tick_when_ready(3)
    reply = env.service.handle({"op": "stop"})
    assert reply["ok"] and reply["status"]["presenting"] is False
    assert is_placeholder(env.tick()[0])

    def ended() -> list[ActivityRecord]:
        return [r for r in env.activity.read_last(10) if r.event == "presentation_ended"]

    wait_until(lambda: bool(ended()))  # written by the activity thread
    assert ended()[-1].outcome == "stopped"


def test_stop_when_idle_is_noop(env: Env) -> None:
    assert env.service.handle({"op": "stop"})["ok"] is True


def test_decoder_stall_over_200ms_fails_to_placeholder(data_home: Path) -> None:
    env = Env(data_home, lambda: FakeVideoReader(200, W, H, block_after=3))
    env.present()
    wait_until(lambda: env.service.queued >= 3)
    shown = env.tick(3)
    assert [int(f[0, 0, 0]) for f in shown] == [0, 1, 2]
    env.tick(1, dt=0.1)  # 100 ms empty: still within budget, repeats nothing new
    frames = env.tick(1, dt=0.15)  # now > 200 ms without a decoded frame
    assert is_placeholder(frames[-1])
    status = env.service.status()
    assert status["presenting"] is False
    assert status["last_failure"]
    wait_until(lambda: any(r.event == "presentation_ended" for r in env.activity.read_last(10)))
    ended = [r for r in env.activity.read_last(10) if r.event == "presentation_ended"]
    assert ended[-1].outcome == "failed"


def test_decode_error_fails_to_placeholder(data_home: Path) -> None:
    env = Env(data_home, lambda: FakeVideoReader(200, W, H, fail_after=2))
    env.present()

    def decoder_failed() -> bool:
        session = env.service._session
        return session is None or session.error is not None

    wait_until(decoder_failed)
    frames = env.tick(4)
    assert is_placeholder(frames[-1])
    assert env.service.status()["presenting"] is False
    wait_until(lambda: any(r.event == "presentation_ended" for r in env.activity.read_last(10)))
    ended = [r for r in env.activity.read_last(10) if r.event == "presentation_ended"]
    assert ended[-1].outcome == "failed"


def test_indicator_toggles_banner_next_frame(env: Env) -> None:
    x0, y0, x1, y1 = indicator_region(W, H)
    env.present()
    plain = env.tick_when_ready(1)[0]
    assert env.service.handle({"op": "indicator", "on": True})["status"]["indicator_on"] is True
    on = env.tick_when_ready(1)[0]
    assert int(on[0, 0, 0]) == 1  # still the recording, outside the banner
    assert np.array_equal(on, draw_indicator(np.full_like(on, 1)))
    env.service.handle({"op": "indicator", "on": False})
    off = env.tick_when_ready(1)[0]
    assert np.array_equal(off, np.full_like(off, 2))
    assert not np.array_equal(on[y0:y1, x0:x1], plain[y0:y1, x0:x1])


def test_indicator_not_drawn_on_placeholder(env: Env) -> None:
    env.service.handle({"op": "indicator", "on": True})
    assert is_placeholder(env.tick()[0])  # "RECORDED VIDEO" over "Camera paused" would mislead


def test_status_fields(env: Env) -> None:
    env.present()
    env.tick_when_ready(2)
    env.clock.advance(10)
    s = env.service.handle({"op": "status"})["status"]
    assert s["service_running"] is True
    assert s["presenting"] is True
    assert s["recording_name"] == "Office"
    assert s["recording_id"] == "a" * 32
    assert s["elapsed_s"] == pytest.approx(10 + 2 / 30, abs=0.01)
    assert (s["canvas_width"], s["canvas_height"]) == (W, H)
    assert s["quality"] == "720p"
    assert s["indicator_on"] is False
    wait_until(lambda: any(r.event == "presented" for r in env.activity.read_last(10)))
    started = [r for r in env.activity.read_last(10) if r.event == "presented"]
    assert started[-1].recording_id == "a" * 32 and started[-1].session_id


def test_shutdown_sends_placeholder_for_half_second_then_closes(env: Env) -> None:
    env.present()
    env.tick_when_ready(2)
    reply = env.service.handle({"op": "shutdown"})
    assert reply == {"ok": True}
    before = len(env.sink.frames)
    env.service.finish()
    tail = env.sink.frames[before:]
    assert len(tail) == 15  # 0.5 s at 30 fps
    assert all(is_placeholder(f) for f in tail)
    assert env.sink.closed
    ended = [r for r in env.activity.read_last(10) if r.event == "presentation_ended"]
    assert ended[-1].outcome == "service_shutdown"


def test_unreadable_file_on_present(data_home: Path) -> None:
    def broken() -> FakeVideoReader:
        from teams_avatar.errors import UnreadableVideoError

        raise UnreadableVideoError("gone")

    env = Env(data_home, broken)
    reply = env.present()
    assert reply["ok"] is False and reply["error"] == "unreadable"
    assert is_placeholder(env.tick()[0])


def test_service_never_imports_camera_capture() -> None:
    tree = ast.parse(Path(service_module.__file__).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{a.name}" for a in node.names)
    assert not any("pyav_capture" in n or "preview_window" in n for n in names), names


def test_failure_record_never_blocks_the_pacer(data_home: Path) -> None:
    """The pacer must hand the failure record to a background thread, not wait on data.lock."""
    import threading

    reader = FakeVideoReader(200, W, H, block_after=3)
    env = Env(data_home, lambda: reader)
    gate = threading.Event()
    real_append = env.activity.append

    def slow_append(record: ActivityRecord) -> None:
        if record.event == "presentation_ended":
            gate.wait(5)
        real_append(record)

    env.activity.append = slow_append  # type: ignore[method-assign]
    assert env.present()["ok"] is True
    started = time.monotonic()
    frames = env.tick(10, dt=0.3)  # the decoder stalls: the presentation fails
    assert time.monotonic() - started < 1.0
    assert is_placeholder(frames[-1])
    gate.set()
    reader.release.set()
    wait_until(lambda: any(r.event == "presentation_ended" for r in env.activity.read_last(10)))
    env.service.handle({"op": "shutdown"})
    env.service.finish()


def test_present_open_is_bounded_below_the_client_budget(
    data_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    monkeypatch.setattr(service_module, "OPEN_READER_TIMEOUT_S", 0.2)
    gate = threading.Event()
    opened: list[FakeVideoReader] = []

    def stuck() -> FakeVideoReader:
        gate.wait(5)
        reader = FakeVideoReader(200, W, H)
        opened.append(reader)
        return reader

    env = Env(data_home, stuck)
    started = time.monotonic()
    reply = env.present()
    assert time.monotonic() - started < 2.0
    # A slow open says nothing about the file: it must not be reported as unreadable.
    assert reply["ok"] is False and reply["error"] == "start_timeout"
    assert env.service.presenting is False
    gate.set()
    wait_until(lambda: bool(opened) and opened[0].closed)  # the late reader is not leaked


def test_start_bounds_stay_under_the_present_budget() -> None:
    from teams_avatar.app import PRESENT_TIMEOUT_S

    worst = (
        service_module.CTL_WAIT_S
        + service_module.OPEN_READER_TIMEOUT_S
        + service_module.START_TIMEOUT_S
    )
    assert worst < PRESENT_TIMEOUT_S


def test_other_requests_wait_less_than_the_default_client_budget() -> None:
    from teams_avatar.control import REPLY_TIMEOUT_S

    assert service_module.CTL_WAIT_S < REPLY_TIMEOUT_S


def test_first_picture_too_slow_is_a_start_timeout_not_unreadable(
    data_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_module, "START_TIMEOUT_S", 0.2)
    reader = FakeVideoReader(200, W, H, block_after=0)
    env = Env(data_home, lambda: reader)
    reply = env.present()
    reader.release.set()
    assert reply["ok"] is False and reply["error"] == "start_timeout"
    assert env.service.presenting is False


def _hold(lock: threading.Lock) -> Callable[[], None]:
    """Hold ``lock`` on another thread (a slow request in progress); return its releaser."""
    taken, release = threading.Event(), threading.Event()

    def run() -> None:
        with lock:
            taken.set()
            release.wait(5)

    threading.Thread(target=run, daemon=True).start()
    taken.wait(2)
    return release.set


def test_busy_control_lock_is_answered_within_the_wait(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_module, "CTL_WAIT_S", 0.1)
    release = _hold(env.service._ctl)
    try:
        for request in ({"op": "stop"}, {"op": "indicator", "on": True}):
            started = time.monotonic()
            reply = env.service.handle(request)
            assert time.monotonic() - started < 1.0
            assert reply["ok"] is False and reply["error"] == "busy"
        started = time.monotonic()
        assert env.present()["error"] == "busy"
        assert time.monotonic() - started < 1.0
    finally:
        release()


def test_shutdown_is_accepted_while_a_request_is_in_progress(env: Env) -> None:
    release = _hold(env.service._ctl)
    try:
        started = time.monotonic()
        assert env.service.handle({"op": "shutdown"}) == {"ok": True}
        assert time.monotonic() - started < 0.5
        assert env.service.stopping
    finally:
        release()


def test_finish_sends_the_tail_before_any_slow_work(env: Env) -> None:
    """Closing the window leaves Windows' 4 s budget; the tail must not wait on locks."""
    import threading

    env.present()
    env.tick_when_ready(2)
    gate = threading.Event()
    real_append = env.activity.append

    def slow_append(record: ActivityRecord) -> None:
        gate.wait(5)
        real_append(record)

    env.activity.append = slow_append  # type: ignore[method-assign]
    release = _hold(env.service._ctl)  # a slow present still holds the control lock
    try:
        env.service.request_shutdown()
        before = len(env.sink.frames)
        done = threading.Thread(target=env.service.finish, daemon=True)
        done.start()
        wait_until(lambda: env.sink.closed, timeout=1.5)
        assert len(env.sink.frames) - before == 15
    finally:
        gate.set()
        release()
        done.join(5)
    ended = [r for r in env.activity.read_last(10) if r.event == "presentation_ended"]
    assert ended and ended[-1].outcome == "service_shutdown"


def test_present_reply_does_not_wait_for_the_activity_log(env: Env) -> None:
    import threading

    gate = threading.Event()
    real_append = env.activity.append

    def slow_append(record: ActivityRecord) -> None:
        gate.wait(5)
        real_append(record)

    env.activity.append = slow_append  # type: ignore[method-assign]
    started = time.monotonic()
    assert env.present()["ok"] is True
    assert time.monotonic() - started < 1.0
    gate.set()
    wait_until(lambda: any(r.event == "presented" for r in env.activity.read_last(10)))


def test_activity_records_do_not_queue_behind_a_blocked_console(data_home: Path) -> None:
    """A console in selection mode blocks window updates; records must still be written."""
    import threading

    reader = FakeVideoReader(200, W, H, block_after=3)
    env = Env(data_home, lambda: reader)
    gate = threading.Event()
    env.service._post(lambda: gate.wait(5))  # the console is stuck
    try:
        assert env.present()["ok"] is True
        env.tick(10, dt=0.3)  # the decoder stalls: the presentation fails
        assert not env.service.presenting
        wait_until(lambda: any(r.event == "presentation_ended" for r in env.activity.read_last(10)))
    finally:
        gate.set()
        reader.release.set()


class TestConsoleShutdown:
    """The console control handler turns a closed window into the normal, orderly shutdown."""

    def test_close_event_requests_shutdown_and_waits_for_finish(self, env: Env) -> None:
        import threading

        from teams_avatar.service import CTRL_CLOSE_EVENT, ConsoleShutdown

        console = ConsoleShutdown(wait_s=3.0)
        console.attach(env.service)
        env.present()
        env.tick_when_ready(2)
        result: list[bool] = []
        worker = threading.Thread(target=lambda: result.append(console.handler(CTRL_CLOSE_EVENT)))
        worker.start()
        wait_until(lambda: env.service.stopping)
        assert worker.is_alive()  # still waiting for finish(), so Windows keeps the process
        env.service.finish()
        console.finished()
        worker.join(2)
        assert result == [True]
        assert env.sink.closed
        ended = [r for r in env.activity.read_last(10) if r.event == "presentation_ended"]
        assert ended[-1].outcome == "service_shutdown"

    def test_wait_is_bounded(self, env: Env) -> None:
        from teams_avatar.service import CTRL_CLOSE_EVENT, ConsoleShutdown

        console = ConsoleShutdown(wait_s=0.1)
        console.attach(env.service)
        started = time.monotonic()
        assert console.handler(CTRL_CLOSE_EVENT) is True
        assert time.monotonic() - started < 1.0
        assert env.service.stopping

    @pytest.mark.parametrize("event", [0, 1, 2, 5, 6])
    def test_handles_every_terminating_event(self, env: Env, event: int) -> None:
        from teams_avatar.service import ConsoleShutdown

        console = ConsoleShutdown(wait_s=0.05)
        console.attach(env.service)
        assert console.handler(event) is True
        assert env.service.stopping

    def test_other_events_and_unattached_handler_pass_through(self, env: Env) -> None:
        from teams_avatar.service import ConsoleShutdown

        console = ConsoleShutdown(wait_s=0.05)
        assert console.handler(2) is False  # no service yet: nothing to shut down gracefully
        console.attach(env.service)
        assert console.handler(3) is False
        assert not env.service.stopping
