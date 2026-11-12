"""Capture mode ladder with a fake device opener and fake clock (research R2)."""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from teams_avatar.adapters.pyav_capture import DEFAULT_LADDER, CameraSource, Mode
from teams_avatar.errors import CameraUnavailableError
from teams_avatar.models import CameraInfo
from tests.fakes import FakeClock

CAM = CameraInfo(name="Integrated Camera", device_name="Integrated Camera")


def test_ladder_order() -> None:
    assert DEFAULT_LADDER == [
        Mode(1920, 1080, mjpeg=True),
        Mode(1920, 1080, mjpeg=False),
        Mode(1280, 720, mjpeg=True),
        Mode(1280, 720, mjpeg=False),
        Mode(960, 540, mjpeg=False),
        Mode(640, 480, mjpeg=False),
    ]


class FakeStream:
    def __init__(self, mode: Mode, fps: float, clock: FakeClock) -> None:
        self.mode, self.fps, self.clock, self.closed = mode, fps, clock, False

    def read(self) -> np.ndarray:
        self.clock.advance(1 / self.fps)
        return np.zeros((self.mode.height, self.mode.width, 3), dtype=np.uint8)

    def close(self) -> None:
        self.closed = True


class Opener:
    """Fails modes in ``refuse``; delivers ``rates[mode]`` fps otherwise (default 30)."""

    def __init__(self, clock: FakeClock, refuse: set[Mode], rates: dict[Mode, float]) -> None:
        self.clock, self.refuse, self.rates = clock, refuse, rates
        self.tried: list[Mode] = []
        self.streams: list[FakeStream] = []

    def __call__(self, camera: CameraInfo, mode: Mode, fps: int) -> FakeStream:
        self.tried.append(mode)
        if mode in self.refuse:
            raise OSError("mode not supported")
        stream = FakeStream(mode, self.rates.get(mode, 30), self.clock)
        self.streams.append(stream)
        return stream


def test_keeps_first_mode_that_opens_and_reaches_27fps() -> None:
    clock = FakeClock()
    refuse = {DEFAULT_LADDER[0], DEFAULT_LADDER[1]}
    opener = Opener(clock, refuse, {DEFAULT_LADDER[2]: 15})
    source = CameraSource(open_device=opener, clock=clock)
    source.open(CAM)
    assert source.mode == Mode(1280, 720, mjpeg=False)
    assert opener.tried == DEFAULT_LADDER[:4]
    assert opener.streams[0].closed  # the too-slow MJPEG stream was released
    frame = source.read()
    assert frame.shape == (720, 1280, 3)
    source.close()
    assert opener.streams[-1].closed


def test_all_modes_fail_is_unusable() -> None:
    clock = FakeClock()
    opener = Opener(clock, set(DEFAULT_LADDER), {})
    with pytest.raises(CameraUnavailableError, match="unusable"):
        CameraSource(open_device=opener, clock=clock).open(CAM)


def test_all_modes_too_slow_is_unusable() -> None:
    clock = FakeClock()
    opener = Opener(clock, set(), {m: 10 for m in DEFAULT_LADDER})
    with pytest.raises(CameraUnavailableError, match="unusable"):
        CameraSource(open_device=opener, clock=clock).open(CAM)
    assert all(s.closed for s in opener.streams)


def test_obs_virtual_camera_refused() -> None:
    clock = FakeClock()
    opener = Opener(clock, set(), {})
    obs = CameraInfo(name="OBS Virtual Camera", device_name="OBS Virtual Camera")
    with pytest.raises(CameraUnavailableError):
        CameraSource(open_device=opener, clock=clock).open(obs)
    assert opener.tried == []


def test_open_timeout() -> None:
    gate = threading.Event()

    def hang(camera: CameraInfo, mode: Mode, fps: int) -> FakeStream:
        gate.wait(5)
        raise OSError("never")

    source = CameraSource(open_device=hang, open_timeout_s=0.2)
    start = time.monotonic()
    with pytest.raises(CameraUnavailableError):
        source.open(CAM)
    gate.set()
    assert time.monotonic() - start < 2


def test_default_open_timeout_is_10s() -> None:
    assert CameraSource().open_timeout_s == 10


class BrightStream(FakeStream):
    """FakeStream whose frames have a chosen brightness."""

    level = 0

    def read(self) -> np.ndarray:
        frame = super().read()
        frame[:] = self.level
        return frame


def test_dark_slow_camera_stops_early_with_clear_reason() -> None:
    clock = FakeClock()
    tried: list[Mode] = []
    streams: list[BrightStream] = []

    def opener(camera: CameraInfo, mode: Mode, fps: int) -> BrightStream:
        tried.append(mode)
        stream = BrightStream(mode, 8, clock)
        streams.append(stream)
        return stream

    with pytest.raises(CameraUnavailableError) as info:
        CameraSource(open_device=opener, clock=clock).open(CAM)
    assert len(tried) == 2  # two modes at the same low rate: stop
    assert "only delivers 8 frames per second" in info.value.message
    assert "too dark" in info.value.message
    assert "Add light" in info.value.message
    assert all(s.closed for s in streams)


def test_slow_bright_camera_has_no_darkness_hint() -> None:
    clock = FakeClock()

    def opener(camera: CameraInfo, mode: Mode, fps: int) -> BrightStream:
        stream = BrightStream(mode, 8, clock)
        stream.level = 200
        return stream

    with pytest.raises(CameraUnavailableError) as info:
        CameraSource(open_device=opener, clock=clock).open(CAM)
    assert "too dark" not in info.value.message


def test_nothing_opens_names_the_likely_causes() -> None:
    clock = FakeClock()
    with pytest.raises(CameraUnavailableError, match="privacy settings"):
        CameraSource(open_device=Opener(clock, set(DEFAULT_LADDER), {}), clock=clock).open(CAM)


def test_stalled_read_is_reported_and_closed_by_the_reader_thread() -> None:
    """Closing a PyAV container while another thread decodes on it is unsafe: never do it."""
    stream_closed = threading.Event()
    gate = threading.Event()
    closers: list[str] = []

    class Stalled:
        def read(self) -> np.ndarray:
            gate.wait(5)
            raise OSError("late")

        def close(self) -> None:
            closers.append(threading.current_thread().name)
            stream_closed.set()

    def opener(camera: CameraInfo, mode: Mode, fps: int) -> Stalled:
        return Stalled()

    start = time.monotonic()
    source = CameraSource(open_device=opener, read_timeout_s=0.2, open_timeout_s=3)
    with pytest.raises(CameraUnavailableError):
        source.open(CAM)
    assert time.monotonic() - start < 3
    assert not stream_closed.is_set()  # the blocked read is still in flight
    gate.set()
    assert stream_closed.wait(2)
    assert closers and set(closers) == {"camera-read"}


def test_read_stall_after_open_raises_and_releases() -> None:
    clock = FakeClock()
    gate = threading.Event()

    class Dies(FakeStream):
        def close(self) -> None:
            super().close()

        def read(self) -> np.ndarray:
            if len(self.__dict__.setdefault("n", [])) >= 60:
                gate.wait(5)
            self.__dict__["n"].append(1)
            return super().read()

    streams: list[Dies] = []

    def opener(camera: CameraInfo, mode: Mode, fps: int) -> Dies:
        stream = Dies(mode, 30, clock)
        streams.append(stream)
        return stream

    source = CameraSource(open_device=opener, clock=clock, read_timeout_s=0.2)
    source.open(CAM)
    with pytest.raises(CameraUnavailableError):
        for _ in range(100):
            source.read()
    gate.set()  # the stalled read returns; the reader thread then closes the stream
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not streams[0].closed:
        time.sleep(0.01)
    assert streams[0].closed
    with pytest.raises(CameraUnavailableError):
        _ = source.size


def test_late_stream_is_closed_at_once() -> None:
    gate = threading.Event()
    clock = FakeClock()
    made: list[FakeStream] = []

    def slow(camera: CameraInfo, mode: Mode, fps: int) -> FakeStream:
        gate.wait(5)
        made.append(FakeStream(mode, 30, clock))
        return made[-1]

    source = CameraSource(open_device=slow, clock=clock, open_timeout_s=0.2)
    with pytest.raises(CameraUnavailableError):
        source.open(CAM)
    gate.set()
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline and not (made and made[0].closed):
        time.sleep(0.01)
    assert made[0].closed


def test_close_never_raises_and_is_idempotent() -> None:
    clock = FakeClock()

    def opener(camera: CameraInfo, mode: Mode, fps: int) -> FakeStream:
        stream = FakeStream(mode, 30, clock)

        def boom() -> None:
            raise OSError("dead device")

        stream.close = boom  # type: ignore[method-assign]
        return stream

    source = CameraSource(open_device=opener, clock=clock)
    source.open(CAM)
    source.close()
    source.close()


def test_reports_delivered_mode_not_requested() -> None:
    clock = FakeClock()

    class Raw(FakeStream):
        mjpeg = False

    def opener(camera: CameraInfo, mode: Mode, fps: int) -> Raw:
        return Raw(mode, 30, clock)

    source = CameraSource(open_device=opener, clock=clock)
    source.open(CAM)
    assert source.mode == Mode(1920, 1080, mjpeg=False)  # MJPEG requested, raw delivered


def test_size_while_closed_raises() -> None:
    with pytest.raises(CameraUnavailableError):
        _ = CameraSource().size


def test_close_racing_a_new_read_still_releases_the_device() -> None:
    """A close() landing between a read's bookkeeping and its request must not leak the device."""
    import queue

    from teams_avatar.adapters.pyav_capture import _TimedStream

    inner = FakeStream(DEFAULT_LADDER[0], 30, FakeClock())
    stream = _TimedStream(inner, read_timeout_s=0.3)

    class GapQueue(queue.Queue[bool]):
        def put(self, item: bool, block: bool = True, timeout: float | None = None) -> None:
            if item:
                # Close from another thread while this read is in its gap.
                closer = threading.Thread(target=stream.close)
                closer.start()
                closer.join(0.2)
            super().put(item, block, timeout)

    gap: GapQueue = GapQueue()
    while not stream._requests.empty():
        gap.put(stream._requests.get())
    real = stream._requests
    stream._requests = gap
    real.put(False)  # retire the original reader thread
    threading.Thread(target=stream._loop, daemon=True).start()
    with pytest.raises((TimeoutError, OSError)):
        stream.read()
    deadline = time.monotonic() + 2
    while not inner.closed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert inner.closed
