"""Named-pipe control channel between the CLI and the camera service (research R4).

Messages are JSON sent with ``send_bytes``/``recv_bytes`` only. ``send``/``recv`` unpickle, which
would let any process that can reach the pipe run code inside the service.
"""

from __future__ import annotations

import _winapi
import getpass
import json
import logging
import os
import re
import secrets
import tempfile
import threading
import time
from collections.abc import Callable
from multiprocessing import AuthenticationError
from multiprocessing.connection import (
    Connection,
    Listener,
    PipeConnection,
    answer_challenge,
    deliver_challenge,
)
from typing import TYPE_CHECKING, Any, Protocol

from teams_avatar.errors import ServiceError, ServiceNotRunning
from teams_avatar.paths import DataPaths

if TYPE_CHECKING:
    _Conn = Connection[Any, Any] | PipeConnection[Any, Any]

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_S = 1.0
REPLY_TIMEOUT_S = 5.0
# How long a connected peer may take to authenticate and send its request before we drop it.
SERVER_IO_TIMEOUT_S = 5.0
KEY_BYTES = 32
MAX_CONNECTIONS = 16
_ERROR_PIPE_BUSY = 231
_ERROR_FILE_NOT_FOUND = 2
_PIPE_READMODE_MESSAGE = 2

Handler = Callable[[dict[str, Any]], dict[str, Any]]
KeySource = bytes | Callable[[], bytes]

_QUALITIES = ("720p", "1080p")
# op -> (required fields, optional fields); a field maps to a type or a tuple of allowed values.
_Rules = dict[str, type | tuple[str, ...]]
_SCHEMAS: dict[str, tuple[_Rules, _Rules]] = {
    "present": (
        {"recording_id": str, "path": str, "quality": _QUALITIES, "indicator": bool},
        {"name": str},
    ),
    "stop": ({}, {}),
    "status": ({}, {}),
    "shutdown": ({}, {}),
    "indicator": ({"on": bool}, {}),
}


class ControlChannel(Protocol):
    """Anything that can send one control request and return the service's reply."""

    def request(self, message: dict[str, Any], timeout_s: float | None = None) -> dict[str, Any]:
        """Send ``message`` and return the reply, spending at most ``timeout_s`` in total.

        ``None`` keeps the default connect (1 s) and reply (5 s) budgets.
        """
        ...


def pipe_name(suffix: str | None = None) -> str:
    """Return the per-user pipe address, with ``-<suffix>`` appended when given."""
    user = re.sub(r"[^A-Za-z0-9_.-]", "_", getpass.getuser())
    name = f"\\\\.\\pipe\\teams-avatar-{user}"
    return f"{name}-{suffix}" if suffix else name


def write_key(paths: DataPaths) -> bytes:
    """Generate a fresh random key, store it atomically in ``service.key`` and return it."""
    key = secrets.token_bytes(KEY_BYTES)
    folder = paths.service_key.parent
    folder.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix="service.key.")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(key.hex().encode("ascii"))
        os.replace(tmp, paths.service_key)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return key


def read_key(paths: DataPaths) -> bytes:
    """Read the key the running service wrote.

    Raises:
        ServiceNotRunning: If the key file is missing (the service never started).
        ServiceError: If the key file is unreadable or malformed.
    """
    try:
        text = paths.service_key.read_text(encoding="ascii").strip()
        return bytes.fromhex(text)
    except FileNotFoundError as exc:
        raise ServiceNotRunning() from exc
    except (OSError, ValueError) as exc:
        raise ServiceError("The camera service key file is unreadable") from exc


def _bad_request(message: str) -> dict[str, Any]:
    return {"ok": False, "error": "bad_request", "message": message}


def validate_request(request: object) -> str | None:
    """Return a problem description if ``request`` breaks the control schema, else None."""
    if not isinstance(request, dict):
        return "request must be a JSON object"
    op = request.get("op")
    if not isinstance(op, str) or op not in _SCHEMAS:
        return "unknown or missing op"
    required, optional = _SCHEMAS[op]
    extra = set(request) - {"op", *required, *optional}
    if extra:
        return f"unexpected field(s): {', '.join(sorted(extra))}"
    for field, kind in {**required, **optional}.items():
        if field not in request:
            if field in required:
                return f"missing field: {field}"
            continue
        value = request[field]
        if isinstance(kind, tuple):
            if value not in kind:
                return f"{field} must be one of {', '.join(kind)}"
        elif not isinstance(value, kind):
            return f"{field} must be {kind.__name__}"
    return None


class _CloseOnce:
    """Closes a connection exactly once, even if the watchdog and worker race."""

    def __init__(self, conn: _Conn) -> None:
        self._conn = conn
        self._lock = threading.Lock()
        self._closed = False

    def close(self) -> None:
        """Close the connection unless it was already closed."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        try:
            self._conn.close()
        except OSError:
            pass


def _open_pipe(address: str, budget_s: float) -> PipeConnection[Any, Any]:
    """Open the client end of a pipe within ``budget_s``, retrying while all instances are busy.

    CPython's own pipe client waits up to 20 s on a busy pipe, so the open is done here.

    Raises:
        ServiceNotRunning: If the pipe is absent or stays busy past the budget.
        ServiceError: On any other OS error.
    """
    deadline = time.monotonic() + budget_s
    while True:
        try:
            handle = _winapi.CreateFile(
                address,
                _winapi.GENERIC_READ | _winapi.GENERIC_WRITE,
                0,
                _winapi.NULL,
                _winapi.OPEN_EXISTING,
                _winapi.FILE_FLAG_OVERLAPPED,
                _winapi.NULL,
            )
            break
        except OSError as exc:
            code = getattr(exc, "winerror", None)
            if code == _ERROR_FILE_NOT_FOUND:
                raise ServiceNotRunning() from exc
            if code != _ERROR_PIPE_BUSY:
                raise ServiceError(f"Cannot reach the camera service ({exc})") from exc
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ServiceNotRunning() from exc
            try:
                _winapi.WaitNamedPipe(address, max(1, int(remaining * 1000)))
            except OSError:
                if time.monotonic() >= deadline:
                    raise ServiceNotRunning() from exc
    _winapi.SetNamedPipeHandleState(handle, _PIPE_READMODE_MESSAGE, None, None)
    return PipeConnection(handle)


class ControlServer:
    """Listens on the pipe and passes validated requests to a handler, one per connection."""

    def __init__(self, address: str, key: bytes) -> None:
        """Create the pipe listener now so clients can connect as soon as this returns."""
        self._address = address
        self._key = key
        self._stopping = threading.Event()
        # No authkey here: Listener.accept() would authenticate inline and let one silent peer
        # block every other client. Authentication runs per connection in a worker thread.
        self._listener = Listener(address, family="AF_PIPE")
        # Bounds worker threads so a loop of idle connections cannot exhaust the process.
        self._slots = threading.BoundedSemaphore(MAX_CONNECTIONS)

    def serve(self, handler: Handler) -> None:
        """Accept connections until ``stop`` is called; handler errors never end the loop."""
        try:
            while not self._stopping.is_set():
                try:
                    conn = self._listener.accept()
                except OSError:
                    if self._stopping.is_set():
                        break
                    logger.exception("control accept failed")
                    time.sleep(0.05)
                    continue
                if self._stopping.is_set():
                    conn.close()
                    break
                if not self._slots.acquire(blocking=False):
                    logger.warning("control connection refused: too many open connections")
                    conn.close()
                    continue
                threading.Thread(
                    target=self._handle_connection, args=(conn, handler), daemon=True
                ).start()
        finally:
            self._listener.close()

    def stop(self) -> None:
        """Ask ``serve`` to return; safe to call from any thread."""
        self._stopping.set()
        # accept() cannot be interrupted, so connect once to wake it.
        try:
            _open_pipe(self._address, 1.0).close()
        except (OSError, ServiceError):
            pass

    def _handle_connection(self, conn: _Conn, handler: Handler) -> None:
        """Authenticate one peer, read its request, reply, and close."""
        closer = _CloseOnce(conn)
        # A peer that stalls is dropped by closing its pipe; the timer covers only the
        # handshake and request read, never the handler.
        watchdog = threading.Timer(SERVER_IO_TIMEOUT_S, closer.close)
        watchdog.daemon = True
        watchdog.start()
        try:
            try:
                deliver_challenge(conn, self._key)
                answer_challenge(conn, self._key)
            except (AuthenticationError, EOFError, OSError):
                logger.warning("control connection failed authentication")
                return
            raw = conn.recv_bytes(1 << 20)
            watchdog.cancel()
            conn.send_bytes(json.dumps(self._respond(raw, handler)).encode("utf-8"))
        except (EOFError, OSError):
            logger.info("control connection dropped before a reply")
        except Exception:
            logger.exception("control connection failed")
        finally:
            watchdog.cancel()
            closer.close()
            self._slots.release()

    @staticmethod
    def _respond(raw: bytes, handler: Handler) -> dict[str, Any]:
        """Decode and validate ``raw``, run the handler, and build the reply."""
        try:
            request = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return _bad_request("request is not valid JSON")
        problem = validate_request(request)
        if problem:
            return _bad_request(problem)
        try:
            return handler(request)
        except Exception:
            logger.exception("control handler failed for op %s", request["op"])
            return {
                "ok": False,
                "error": "internal",
                "message": "the service failed to handle the request",
            }


class ControlClient:
    """Sends one request to the service per call."""

    def __init__(self, address: str, key: KeySource) -> None:
        """Remember the pipe address and key (or a callable returning the current key)."""
        self._address = address
        self._key = key

    def request(self, message: dict[str, Any], timeout_s: float | None = None) -> dict[str, Any]:
        """Send ``message`` and return the decoded reply.

        Args:
            message: Control request.
            timeout_s: Upper bound for connect plus reply; the default budgets still apply.

        Raises:
            ServiceNotRunning: If nothing listens on the pipe within 1 s.
            ServiceError: On a wrong key, no reply in time, or a malformed reply.
        """
        key = self._key() if callable(self._key) else self._key
        started = time.monotonic()
        total = timeout_s if timeout_s is not None else CONNECT_TIMEOUT_S + REPLY_TIMEOUT_S
        conn = _open_pipe(self._address, min(CONNECT_TIMEOUT_S, total))
        left = max(0.05, total - (time.monotonic() - started))
        # An explicit total replaces the default 5 s reply cap (slow requests such as present).
        reply_budget = left if timeout_s is not None else min(REPLY_TIMEOUT_S, left)
        closer = _CloseOnce(conn)
        outcome: list[bytes | BaseException] = []

        def exchange() -> None:
            try:
                answer_challenge(conn, key)
                deliver_challenge(conn, key)
                conn.send_bytes(json.dumps(message).encode("utf-8"))
                outcome.append(conn.recv_bytes())
            except BaseException as exc:
                outcome.append(exc)

        # Handshake, request and reply share one deadline so a mute server cannot hang us.
        worker = threading.Thread(target=exchange, daemon=True)
        worker.start()
        worker.join(reply_budget)
        if worker.is_alive():
            closer.close()
            raise ServiceError("The camera service did not answer in time")
        closer.close()
        result = outcome[0]
        if isinstance(result, AuthenticationError):
            raise ServiceError("The camera service rejected the connection key")
        if isinstance(result, BaseException):
            raise ServiceError("The camera service closed the connection") from result
        try:
            reply = json.loads(result.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ServiceError("The camera service sent an unreadable reply") from exc
        if not isinstance(reply, dict):
            raise ServiceError("The camera service sent an unreadable reply")
        return reply
