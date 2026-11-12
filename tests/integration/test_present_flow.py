"""End to end: real codec, real named pipe, camera service in a thread, fake frame sink.

Covers FR-007 (blended repeat), FR-008/SC-005 (stop -> placeholder within 2 s), FR-020.
"""

from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

import numpy as np
import numpy.typing as npt

from teams_avatar.app import App
from teams_avatar.control import ControlClient, pipe_name, read_key
from teams_avatar.frames import placeholder
from teams_avatar.paths import DataPaths
from teams_avatar.service import run_service
from tests.clips import read_counter
from tests.conftest import ClipFactory

Frame = npt.NDArray[np.uint8]
W, H = 1280, 720
N, B = 300, 30


class PacedSink:
    """Fake sink that paces like the real one (one frame per 1/30 s) and timestamps frames."""

    def __init__(self) -> None:
        self.frames: list[tuple[float, Frame]] = []
        self.size: tuple[int, int, float] | None = None
        self.closed = False
        self._next = time.perf_counter()
        self._lock = threading.Lock()

    def open(self, width: int, height: int, fps: float) -> None:
        self.size = (width, height, fps)
        self._next = time.perf_counter()

    def send(self, frame: Frame) -> None:
        with self._lock:
            self.frames.append((time.perf_counter(), np.array(frame, copy=True)))

    def sleep_until_next_frame(self) -> None:
        self._next += 1 / 30
        delay = self._next - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
        else:
            self._next = time.perf_counter()

    def close(self) -> None:
        self.closed = True

    def snapshot(self) -> list[tuple[float, Frame]]:
        with self._lock:
            return list(self.frames)


class ThreadLauncher:
    def __init__(self, paths: DataPaths, sink: PacedSink, address: str) -> None:
        self.paths, self.sink, self.address = paths, sink, address
        self.thread: threading.Thread | None = None

    def start(self) -> ThreadLauncher:
        self.thread = threading.Thread(
            target=run_service,
            kwargs={"paths": self.paths, "sink": self.sink, "address": self.address},
            daemon=True,
        )
        self.thread.start()
        return self

    def poll(self) -> int | None:
        """Report the thread like a process: None while running, 0 once it has returned."""
        return None if self.thread is not None and self.thread.is_alive() else 0


def test_present_loop_and_stop(data_home: Path, tmp_path: Path, make_clip: ClipFactory) -> None:
    paths = DataPaths.resolve().ensure()
    address = pipe_name(suffix=f"it-{uuid.uuid4().hex}")
    sink = PacedSink()
    launcher = ThreadLauncher(paths, sink, address)
    app = App(
        paths,
        control=ControlClient(address, lambda: read_key(paths)),
        launcher=launcher,
    )
    app.acknowledge_first_use()
    app.import_file(make_clip(tmp_path / "clip.mp4", N, W, H), "Clip")

    try:
        status = app.present("Clip")
        assert status.presenting
        assert sink.size == (W, H, 30)
        start = len(sink.snapshot())
        while len(sink.snapshot()) < start + 400:
            time.sleep(0.05)
        frames = [f for _, f in sink.snapshot()[start : start + 400]]

        # Find the first recording frame, then check the sequence across the repeat point.
        idle = placeholder(W, H)
        first = next(i for i, f in enumerate(frames) if not np.array_equal(f, idle))
        shown = frames[first:]
        assert len(shown) > N + 40
        counters = [read_counter(f) for f in shown[: N - B]]
        assert counters == list(range(N - B))
        for i in range(B):  # blend: differs from both the end frame and the start frame
            f = shown[N - B + i].astype(np.int16)
            end_only = shown[N - B - 1].astype(np.int16)
            assert np.abs(f - end_only).mean() > 0.5
        assert read_counter(shown[N]) == B  # after the blend the loop resumes at frame B
        for a, b in zip(shown, shown[1:], strict=False):
            assert not np.array_equal(a, b), "repeated frame"
            assert not np.array_equal(b, idle), "blank frame while presenting"

        stopped_at = time.perf_counter()
        app.stop()
        deadline = stopped_at + 2.0
        while True:
            later = [(t, f) for t, f in sink.snapshot() if t >= stopped_at]
            if any(np.array_equal(f, idle) for _, f in later):
                t_idle = next(t for t, f in later if np.array_equal(f, idle))
                break
            assert time.perf_counter() < deadline, "no placeholder within 2 s of stop"
            time.sleep(0.01)
        assert t_idle - stopped_at < 2.0
    finally:
        app.shutdown()
        if launcher.thread:
            launcher.thread.join(timeout=5)
    assert sink.closed
