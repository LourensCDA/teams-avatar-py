"""The camera service: owns the virtual camera for a whole meeting (research R4, R5-R7, R11).

Three kinds of thread keep a stalled decode from ever freezing the user's picture:

* the *pacer* sends exactly one frame per ``sink.sleep_until_next_frame()``, always;
* a *decoder* per presentation fills a bounded read-ahead queue from ``LoopPlayer`` (all seeks
  happen there, never on the pacer);
* the *control* thread serves the named pipe and calls ``CameraService.handle``.

The service never opens a physical camera, so a failed presentation can only fall back to the
placeholder, never to the live webcam.
"""

from __future__ import annotations

import ctypes
import gc
import logging
import queue
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import cv2
import numpy as np
from numpy.typing import NDArray

from teams_avatar import logging_setup
from teams_avatar.activity import ActivityLog
from teams_avatar.adapters.pyav_codec import PyAVReader, VideoReader
from teams_avatar.control import ControlServer, pipe_name, write_key
from teams_avatar.errors import DataFileError, TeamsAvatarError, VirtualCameraError
from teams_avatar.frames import draw_indicator, placeholder
from teams_avatar.locks import ServiceLock
from teams_avatar.loop_player import LoopPlayer
from teams_avatar.models import CANVAS_SIZES, ActivityRecord, PresentationStatus, Quality, Settings
from teams_avatar.paths import DataPaths
from teams_avatar.service_window import ServiceWindow
from teams_avatar.settings import SettingsStore

if TYPE_CHECKING:
    from teams_avatar.adapters.virtual_camera import FrameSink

logger = logging.getLogger(__name__)

Frame = NDArray[np.uint8]

FPS = 30
# Frames the decoder may run ahead. Bigger hides longer seeks but costs memory (1080p: 6 MB each).
READ_AHEAD = 30
# Once the queue is full the decoder sleeps until it drains to this many frames (0.33 s cushion).
REFILL_BELOW = 10
# Time without a new decoded picture after which the presentation is declared failed.
STALL_LIMIT_S = 0.2
# Real-time bound for the first picture to be decoded when a presentation starts.
START_TIMEOUT_S = 5.0
# Real-time bound for opening the recording file. With START_TIMEOUT_S this stays under the
# client's 15 s present budget (app.PRESENT_TIMEOUT_S).
OPEN_READER_TIMEOUT_S = 4.0
# Placeholder frames sent before the camera closes, so a connected meeting app does not freeze
# on the user's face (0.5 s).
SHUTDOWN_FRAMES = 15
# Longest a request waits for another one (e.g. a slow present) to finish. Present's worst case,
# CTL_WAIT_S + OPEN_READER_TIMEOUT_S + START_TIMEOUT_S, stays under app.PRESENT_TIMEOUT_S, and
# stop/indicator stay under the client's default 5 s reply budget.
CTL_WAIT_S = 3.0
# How long finish waits for queued activity records to be written.
_RECORDS_JOIN_S = 1.5
# How long the control pipe stays up when the virtual camera cannot be opened.
UNAVAILABLE_GRACE_S = 15.0
_THREAD_JOIN_S = 1.0


CTRL_C_EVENT = 0
CTRL_BREAK_EVENT = 1
CTRL_CLOSE_EVENT = 2
CTRL_LOGOFF_EVENT = 5
CTRL_SHUTDOWN_EVENT = 6
_TERMINATING_EVENTS = frozenset(
    {CTRL_C_EVENT, CTRL_BREAK_EVENT, CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT}
)
# Windows kills a process 5 s after a close/logoff/shutdown handler starts; stay below that.
CONSOLE_WAIT_S = 4.0


class ConsoleShutdown:
    """Console control handler: closing the window runs the normal orderly shutdown.

    Without it the process dies at once, skipping the 0.5 s placeholder tail and the
    ``presentation_ended`` record, so a meeting could freeze on the user's face.
    """

    def __init__(self, wait_s: float = CONSOLE_WAIT_S) -> None:
        """Create a handler that waits up to ``wait_s`` for ``CameraService.finish``."""
        self._wait_s = wait_s
        self._service: CameraService | None = None
        self._done = threading.Event()
        self._callback: Any = None  # keeps the ctypes thunk alive

    def attach(self, service: CameraService) -> None:
        """Register the service whose shutdown the handler requests."""
        self._service = service

    def finished(self) -> None:
        """Signal that ``finish`` completed, releasing a handler that is waiting."""
        self._done.set()

    def handler(self, ctrl_type: int) -> bool:
        """Handle one console event; return True when it was handled (no default kill).

        Runs on a thread Windows creates. Events that do not end the process, or arrive
        before a service exists, are left to the default handler.
        """
        service = self._service
        if ctrl_type not in _TERMINATING_EVENTS or service is None:
            return False
        service.request_shutdown()
        self._done.wait(self._wait_s)
        return True

    def install(self) -> bool:
        """Register this handler with Windows; return whether that succeeded."""
        from ctypes import wintypes

        thunk_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
        self._callback = thunk_type(lambda ctrl_type: self.handler(int(ctrl_type)))
        return bool(ctypes.windll.kernel32.SetConsoleCtrlHandler(self._callback, True))


@dataclass
class _Session:
    """One presentation (data-model "Presentation Session") with its decoder state."""

    session_id: str
    recording_id: str
    name: str
    started: float
    width: int
    height: int
    player: LoopPlayer
    reader: VideoReader
    frames: queue.Queue[Frame] = field(default_factory=lambda: queue.Queue(READ_AHEAD))
    stop: threading.Event = field(default_factory=threading.Event)
    first: threading.Event = field(default_factory=threading.Event)
    error: str | None = None
    last_frame: Frame | None = None
    last_new: float = 0.0
    thread: threading.Thread | None = None


def _reason(exc: BaseException) -> str:
    """Return a plain-language reason for a decoder failure."""
    if isinstance(exc, TeamsAvatarError):
        return exc.cause
    return f"the recording could not be decoded ({type(exc).__name__})"


class CameraService:
    """State machine behind the virtual camera: idle placeholder or a looping recording.

    ``tick`` is the pacer step and ``handle`` serves control requests; both are thread-safe
    against each other. Slow work never delays a frame or a reply: activity records go to a
    writer thread (``data.lock`` may be held by a CLI command) and the console to a relay thread
    (a console in selection mode blocks writers), so neither can hold up the other.
    """

    def __init__(
        self,
        sink: FrameSink,
        canvas: tuple[int, int],
        quality: Quality,
        indicator_on: bool,
        open_reader: Callable[[Path], VideoReader],
        activity: ActivityLog,
        clock: Callable[[], float] = time.monotonic,
        window: ServiceWindow | None = None,
    ) -> None:
        """Bind the service to its sink, canvas and injected collaborators."""
        self._sink = sink
        self._w, self._h = canvas
        self._quality: Quality = quality
        self._indicator_on = indicator_on
        self._open_reader = open_reader
        self._activity = activity
        self._clock = clock
        self._window = window
        self._ctl = threading.Lock()  # serialises control requests
        self._state = threading.Lock()  # guards the fields the pacer reads; held briefly
        self._session: _Session | None = None
        self._last_failure: str | None = None
        self._stopping = threading.Event()
        self._notes: queue.SimpleQueue[Callable[[], object] | None] = queue.SimpleQueue()
        self._relay: threading.Thread | None = None
        self._records: queue.SimpleQueue[ActivityRecord | None] = queue.SimpleQueue()
        self._writer: threading.Thread | None = None

    @property
    def queued(self) -> int:
        """Number of decoded frames waiting for the pacer."""
        session = self._session
        return session.frames.qsize() if session else 0

    @property
    def presenting(self) -> bool:
        """True while a recording is being presented."""
        return self._session is not None

    def request_shutdown(self) -> None:
        """Ask the pacer to stop without taking any lock (safe from a signal-like thread)."""
        self._stopping.set()

    @property
    def stopping(self) -> bool:
        """True once a ``shutdown`` request has been received."""
        return self._stopping.is_set()

    def open(self) -> None:
        """Open the sink once at the canvas size; it stays open until ``finish``.

        Raises:
            VirtualCameraError: If OBS Virtual Camera is missing or in use.
        """
        # OpenCV's worker threads spin between calls; one thread is plenty at 30 fps.
        cv2.setNumThreads(1)
        self._sink.open(self._w, self._h, FPS)
        self._relay = threading.Thread(target=self._relay_loop, name="window", daemon=True)
        self._relay.start()
        self._writer = threading.Thread(target=self._write_loop, name="activity", daemon=True)
        self._writer.start()
        if self._window is not None:
            self._post(self._window.started)

    def pace(self, keep_going: Callable[[], bool] = lambda: True) -> None:
        """Run the pacer on this thread until shutdown is requested or ``keep_going`` is false."""
        while not self._stopping.is_set() and keep_going():
            self.tick()
            self._sink.sleep_until_next_frame()

    def tick(self) -> None:
        """Send exactly one frame: the next recording frame or the placeholder."""
        ended: tuple[_Session, str] | None = None
        with self._state:
            session = self._session
            frame: Frame | None = None
            if session is not None:
                frame, failure = self._next_frame(session)
                if failure is not None:
                    self._session = None
                    self._last_failure = failure
                    ended = (session, failure)
            indicator_on = self._indicator_on
        if frame is None:
            out: Frame = placeholder(self._w, self._h)
        elif indicator_on:
            out = draw_indicator(frame)  # returns a copy, so shared frames stay untouched
        else:
            out = frame
        self._sink.send(out)
        if ended is not None:
            self._end(ended[0], "failed", ended[1])

    def _next_frame(self, session: _Session) -> tuple[Frame | None, str | None]:
        """Pick the frame for this tick, or a failure reason (state lock held)."""
        if session.error is not None:
            return None, session.error
        now = self._clock()
        try:
            frame = session.frames.get_nowait()
        except queue.Empty:
            if now - session.last_new > STALL_LIMIT_S:
                return None, "the recording stopped delivering pictures for more than 0.2 seconds"
            # A short gap repeats the last picture rather than dropping to the placeholder.
            return session.last_frame, None
        session.last_frame = frame
        session.last_new = now
        return frame, None

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        """Serve one control request (``contracts/app-api.md``); safe to call from any thread."""
        op = request.get("op")
        if op == "status":
            return {"ok": True, "status": self.status()}
        if op == "shutdown":
            # Needs no lock: the pacer sees the flag and finish() ends any presentation.
            self._stopping.set()
            return {"ok": True}
        if op not in ("present", "stop", "indicator"):
            return _error("bad_request", "unknown or missing op")
        if not self._ctl.acquire(timeout=CTL_WAIT_S):
            return _error(
                "busy",
                "The camera service is busy with another request. Try again in a few seconds.",
            )
        try:
            if op == "present":
                return self._present(request)
            if op == "stop":
                self._stop_presentation("stopped", None)
                return {"ok": True, "status": self.status()}
            return self._set_indicator(bool(request.get("on")))
        finally:
            self._ctl.release()

    def status(self) -> dict[str, Any]:
        """Return the presentation status as the JSON form of ``PresentationStatus``."""
        with self._state:
            session = self._session
            status = PresentationStatus(
                service_running=True,
                presenting=session is not None,
                recording_id=session.recording_id if session else None,
                recording_name=session.name if session else None,
                elapsed_s=(self._clock() - session.started) if session else None,
                canvas_width=self._w,
                canvas_height=self._h,
                quality=self._quality,
                indicator_on=self._indicator_on,
                recording_width=session.width if session else None,
                recording_height=session.height if session else None,
                last_failure=self._last_failure,
            )
        return status.to_dict()

    def finish(self) -> None:
        """End any presentation, show the placeholder for 0.5 s, then close the sink.

        Must run after the pacer has stopped, so two threads never send at once. The tail is
        sent first and no lock is taken before it: closing the console leaves Windows' 4 s
        budget, and a meeting must never be left frozen on the user's face. A present still in
        progress sees ``stopping`` and does not install its session.
        """
        self._stopping.set()
        with self._state:
            session, self._session = self._session, None
        if session is not None:
            session.stop.set()
        try:
            idle = placeholder(self._w, self._h)
            for _ in range(SHUTDOWN_FRAMES):
                self._sink.send(idle)
                self._sink.sleep_until_next_frame()
        except Exception:
            logger.exception("could not send the closing placeholder")
        finally:
            self._sink.close()
        if session is not None:
            self._end(session, "service_shutdown", None)
            if session.thread is not None:
                session.thread.join(_THREAD_JOIN_S)
        if self._window is not None:
            self._show(lambda w: w.idle("Camera service stopped."))
        self._records.put(None)
        self._notes.put(None)
        if self._writer is not None:
            self._writer.join(_RECORDS_JOIN_S)
        if self._relay is not None:
            self._relay.join(_THREAD_JOIN_S)

    def _present(self, request: dict[str, Any]) -> dict[str, Any]:
        """Start presenting a recording (control lock held)."""
        if self._stopping.is_set():
            return _error("shutting_down", "The camera service is shutting down.")
        recording_id = str(request["recording_id"])
        with self._state:
            current = self._session
        if current is not None:
            if current.recording_id == recording_id:
                return {"ok": True, "status": self.status()}
            return _error(
                "already_presenting",
                f'"{current.name}" is already being presented. Stop it first.',
            )
        name = str(request.get("name") or recording_id[:8])
        try:
            reader = self._open_reader_bounded(Path(str(request["path"])))
        except TimeoutError:
            return _start_timeout("opening the recording took too long")
        except Exception as exc:
            return _error("unreadable", _message(exc))
        session = _Session(
            session_id=uuid.uuid4().hex,
            recording_id=recording_id,
            name=name,
            started=self._clock(),
            width=reader.width,
            height=reader.height,
            player=LoopPlayer(reader, self._w, self._h),
            reader=reader,
        )
        session.thread = threading.Thread(
            target=self._decode, args=(session,), name="decoder", daemon=True
        )
        session.thread.start()
        # Wait here (bounded) so "present returns" means the recording is about to be shown.
        session.first.wait(START_TIMEOUT_S)
        if session.error is not None or session.frames.empty():
            session.stop.set()
            reason = session.error or "the first picture took too long to decode"
            logger.warning("presentation did not start", extra={"reason": reason})
            if session.error is None:
                # Slowness (a busy disk, a sleeping laptop) says nothing about the file.
                return _start_timeout(reason)
            return _error("unreadable", f"The video cannot be read: {reason}.")
        with self._state:
            if self._stopping.is_set():
                session.stop.set()
                return _error("shutting_down", "The camera service is shutting down.")
            session.started = session.last_new = self._clock()
            self._session = session
            self._last_failure = None
            if "indicator" in request:
                self._indicator_on = bool(request["indicator"])
            indicator_on = self._indicator_on
        self._record(session, "presented", "started", None)
        with logging_setup.session(session.session_id):
            logger.info("presentation started", extra={"recording_id": recording_id})
        if self._window is not None:
            self._show(lambda w: w.presenting(name, indicator_on))
        return {"ok": True, "status": self.status()}

    def _open_reader_bounded(self, path: Path) -> VideoReader:
        """Open ``path`` on a helper thread, giving up after ``OPEN_READER_TIMEOUT_S``.

        A reader that arrives after the deadline is closed, never leaked.
        """
        lock = threading.Lock()
        done = threading.Event()
        box: list[VideoReader | BaseException] = []
        abandoned = False

        def work() -> None:
            try:
                outcome: VideoReader | BaseException = self._open_reader(path)
            except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread
                outcome = exc
            with lock:
                if not abandoned:
                    box.append(outcome)
                    done.set()
                    return
            if not isinstance(outcome, BaseException):
                try:
                    outcome.close()
                except Exception:
                    logger.exception("could not close a late reader")

        threading.Thread(target=work, name="open-reader", daemon=True).start()
        if not done.wait(OPEN_READER_TIMEOUT_S):
            with lock:
                if not box:
                    abandoned = True
            if abandoned:
                raise TimeoutError("opening the recording took too long")
        result = box[0]
        if isinstance(result, BaseException):
            raise result
        return result

    def _stop_presentation(self, outcome: str, detail: str | None) -> None:
        """End the current presentation if there is one (control lock held)."""
        with self._state:
            session, self._session = self._session, None
        if session is not None:
            self._end(session, outcome, detail)

    def _set_indicator(self, on: bool) -> dict[str, Any]:
        """Change the indicator; it applies from the next frame (control lock held)."""
        with self._state:
            self._indicator_on = on
            session = self._session
        state = "ON" if on else "OFF"
        logger.info("indicator changed", extra={"indicator": state})
        if self._window is not None:
            if session is not None:
                name = session.name
                self._show(lambda w: w.presenting(name, on))
            else:
                self._show(lambda w: w.note(f"Recording indicator: {state}."))
        return {"ok": True, "status": self.status()}

    def _end(self, session: _Session, outcome: str, detail: str | None) -> None:
        """Stop the decoder and record, log and show how the presentation ended."""
        session.stop.set()
        self._record(session, "presentation_ended", outcome, detail)
        with logging_setup.session(session.session_id):
            logger.info("presentation ended", extra={"outcome": outcome, "detail": detail})
        window = self._window
        if window is None:
            return
        if outcome == "stopped":
            self._show(lambda w: w.idle("Presentation stopped."))
        elif outcome == "failed":
            reason = detail or "unknown reason"
            self._show(lambda w: w.failed(session.name, reason))

    def _record(self, session: _Session, event: str, outcome: str, detail: str | None) -> None:
        """Queue an activity record for the writer thread (written at once if it is not running).

        ``data.lock`` may be held by a CLI command for seconds, so neither the pacer nor a
        control reply ever waits on it.
        """
        record = ActivityRecord(
            at=datetime.now(UTC),
            event=event,
            recording_id=session.recording_id,
            recording_name=session.name,
            session_id=session.session_id,
            outcome=outcome,
            detail=detail,
        )
        if self._writer is not None and self._writer.is_alive():
            self._records.put(record)
        else:
            self._append(record)

    def _append(self, record: ActivityRecord) -> None:
        """Write one activity record; a failing log must never break the camera."""
        try:
            self._activity.append(record)
        except Exception:
            logger.exception("could not write the activity log")

    def _write_loop(self) -> None:
        """Writer thread: append queued activity records in order until told to stop."""
        while True:
            record = self._records.get()
            if record is None:
                return
            self._append(record)

    def _decode(self, session: _Session) -> None:
        """Decoder thread: keep the read-ahead queue full; report errors through the session."""
        with logging_setup.session(session.session_id):
            try:
                while not session.stop.is_set():
                    frame = session.player.next_frame()
                    while not session.stop.is_set():
                        try:
                            session.frames.put(frame, timeout=0.1)
                            break
                        except queue.Full:
                            continue
                    session.first.set()
                    # Refill in bursts: the same work costs about 2.5x less CPU done back to back
                    # than spread one frame per tick, where the core never leaves a low-power state.
                    if session.frames.full():
                        while not session.stop.is_set() and session.frames.qsize() > REFILL_BELOW:
                            session.stop.wait(0.02)
            except Exception as exc:
                logger.exception("decoder failed")
                session.error = _reason(exc)
            finally:
                session.first.set()
                try:
                    session.reader.close()
                except Exception:
                    logger.exception("could not close the reader")

    def _show(self, action: Callable[[ServiceWindow], None]) -> None:
        """Queue an update of the service window, if there is one."""
        window = self._window
        if window is not None:
            self._notes.put(lambda: action(window))

    def _post(self, action: Callable[[], object]) -> None:
        """Queue a console update for the relay thread."""
        self._notes.put(action)

    def _relay_loop(self) -> None:
        """Run console updates in order; a blocked console only blocks this thread."""
        while True:
            action = self._notes.get()
            if action is None:
                return
            try:
                action()
            except Exception:
                logger.exception("service window update failed")


def _message(exc: Exception) -> str:
    """Return the user-facing message for an error opening a recording."""
    return exc.message if isinstance(exc, TeamsAvatarError) else f"The video cannot be read: {exc}"


def _start_timeout(reason: str) -> dict[str, Any]:
    """Build the reply for a presentation that did not start in time (the file may be fine)."""
    return _error("start_timeout", f"The recording did not start in time: {reason}.")


def _error(code: str, message: str) -> dict[str, Any]:
    """Build an error reply."""
    return {"ok": False, "error": code, "message": message}


def _load_quality(paths: DataPaths) -> tuple[Quality, bool]:
    """Return the saved quality and indicator setting, falling back to defaults."""
    try:
        settings = SettingsStore(paths).load()
    except DataFileError:
        logger.exception("settings unreadable; using defaults")
        settings = Settings()
    return settings.presentation_quality, settings.indicator_on


def run_service(
    paths: DataPaths,
    sink: FrameSink,
    address: str | None = None,
    window: ServiceWindow | None = None,
    console: ConsoleShutdown | None = None,
) -> int:
    """Run the camera service until shutdown and return the process exit code.

    Returns 0 at once if another service already holds ``service.lock``. ``console`` is told
    when the service has finished closing, which releases its control handler.
    """
    lock = ServiceLock(paths.service_lock)
    if not lock.try_acquire():
        return 0
    try:
        return _run_locked(paths, sink, address or pipe_name(), window, console)
    finally:
        lock.release()
        if console is not None:
            console.finished()


def _run_locked(
    paths: DataPaths,
    sink: FrameSink,
    address: str,
    window: ServiceWindow | None,
    console: ConsoleShutdown | None = None,
) -> int:
    """Body of ``run_service`` once the single-instance lock is held."""
    quality, indicator_on = _load_quality(paths)
    canvas = CANVAS_SIZES[quality]
    key = write_key(paths)
    service = CameraService(
        sink=sink,
        canvas=canvas,
        quality=quality,
        indicator_on=indicator_on,
        open_reader=PyAVReader,
        activity=ActivityLog(paths),
        window=window,
    )
    try:
        service.open()
    except VirtualCameraError as exc:
        logger.error("virtual camera unavailable", extra={"kind": exc.kind})
        if window is not None:
            window.note(f"Virtual camera unavailable: {exc.message}")
        return _serve_unavailable(address, key, exc, canvas, quality)
    if console is not None:
        console.attach(service)
    control = ControlServer(address, key)
    thread = threading.Thread(target=control.serve, args=(service.handle,), name="control")
    thread.daemon = True
    thread.start()
    code = 0
    try:
        service.pace(thread.is_alive)
        if not service.stopping:
            logger.error("control thread ended unexpectedly")
            code = 1
    except KeyboardInterrupt:
        logger.info("interrupted; shutting down")
    except Exception:
        logger.exception("pacer failed")
        code = 1
    finally:
        service.finish()
        if console is not None:
            console.finished()
        control.stop()
        thread.join(2.0)
    return code


def _serve_unavailable(
    address: str, key: bytes, exc: VirtualCameraError, canvas: tuple[int, int], quality: Quality
) -> int:
    """Keep the pipe up briefly so the CLI can learn why presenting is impossible."""
    code = f"virtual_camera_{exc.kind}"
    done = threading.Event()
    status = PresentationStatus(
        service_running=True, canvas_width=canvas[0], canvas_height=canvas[1], quality=quality
    ).to_dict()

    def handler(request: dict[str, Any]) -> dict[str, Any]:
        op = request.get("op")
        if op == "present":
            return _error(code, exc.message)
        if op == "shutdown":
            done.set()
            return {"ok": True}
        return {"ok": True, "status": status}

    control = ControlServer(address, key)
    thread = threading.Thread(target=control.serve, args=(handler,), name="control", daemon=True)
    thread.start()
    try:
        done.wait(UNAVAILABLE_GRACE_S)
    finally:
        control.stop()
        thread.join(2.0)
    return exc.exit_code


def serve_main() -> int:
    """Entry point for ``python -m teams_avatar service``: real sink, window and logging."""
    from teams_avatar.adapters.virtual_camera import ObsFrameSink

    paths = DataPaths.resolve().ensure()
    logging_setup.configure(paths)
    # Moving start-up objects out of the collector keeps full collections short mid-meeting.
    gc.collect()
    gc.freeze()
    winmm = ctypes.windll.winmm
    winmm.timeBeginPeriod(1)  # 1 ms timer so the 33 ms pacing does not wobble on the 15 ms tick
    console = ConsoleShutdown()
    if not console.install():
        logger.warning("could not install the console control handler")
    try:
        return run_service(paths, ObsFrameSink(), window=ServiceWindow(), console=console)
    finally:
        winmm.timeEndPeriod(1)
