"""Control channel between the CLI and the camera service (research R4).

A per-user Windows named pipe carries one JSON request and one JSON reply per connection. Only
``send_bytes``/``recv_bytes`` are used, never ``send``/``recv``, because those unpickle and would
let any connecting process run code in the service. The pipe is authenticated with a random key
kept in ``paths.service_key``.
"""

from __future__ import annotations

import _winapi
import getpass
import json
import logging
import re
import secrets
import threading
import time
from collections.abc import Callable
from multiprocessing import AuthenticationError
from multiprocessing.connection import (
    Listener,
    PipeConnection,
    answer_challenge,
    deliver_challenge,
)
from typing import Any, Protocol

from teams_avatar.errors import ServiceError, ServiceNotRunning
from teams_avatar.paths import DataPaths

_log = logging.getLogger(__name__)

CONNECT_TIMEOUT_S = 1.0
REPLY_TIMEOUT_S = 5.0
# A client that connects but never sends is dropped after this, freeing its worker thread.
SERVER_READ_TIMEOUT_S = 5.0
_KEY_BYTES = 32

Handler = Callable[[dict[str, Any]], dict[str, Any]]
KeySource = bytes | Callable[[], bytes]
_Check = Callable[[object], bool]

_ERROR_PIPE_BUSY = 231


class ControlChannel(Protocol):
    """Anything that can send one request to the service and return its reply."""

    def request(self, message: dict[str, Any]) -> dict[str, Any]:
        """Send ``message`` and return the reply."""
        ...


def pipe_name(suffix: str | None = None) -> str:
    """Return the per-user pipe address, with ``-<suffix>`` appended when given."""
    user = re.sub(r"[^A-Za-z0-9_.-]", "_", getpass.getuser()) or "user"
    name = f"\\\\.\\pipe\\teams-avatar-{user}"
    return f"{name}-{suffix}" if suffix else name


def write_key(paths: DataPaths) -> bytes:
    """Create a fresh random key, store it in the key file and return it.

    Called by the service before it starts listening, so a client that sees the pipe can always
    read a matching key.
    """
    key = secrets.token_bytes(_KEY_BYTES)
    paths.service_key.parent.mkdir(parents=True, exist_ok=True)
    paths.service_key.write_bytes(key)
    return key


def read_key(paths: DataPaths) -> bytes:
    """Return the key the running service wrote.

    Raises:
        ServiceNotRunning: If the key file does not exist.
    """
    try:
        return paths.service_key.read_bytes()
    except FileNotFoundError:
        raise ServiceNotRunning() from None


def _bad(message: str) -> dict[str, Any]:
    """Build the standard rejection reply."""
    return {"ok": False, "error": "bad_request", "message": message}


def _is_str(value: object) -> bool:
    """Return whether ``value`` is a string."""
    return isinstance(value, str)


def _is_bool(value: object) -> bool:
    """Return whether ``value`` is a real boolean (not 0/1)."""
    return isinstance(value, bool)


def _is_quality(value: object) -> bool:
    """Return whether ``value`` is a supported output quality."""
    return value in ("720p", "1080p")


# op -> (required fields, optional fields)
_SCHEMA: dict[str, tuple[dict[str, _Check], dict[str, _Check]]] = {
    "present": (
        {
            "recording_id": _is_str,
            "path": _is_str,
            "quality": _is_quality,
            "indicator": _is_bool,
        },
        {"name": _is_str},
    ),
    "stop": ({}, {}),
    "status": ({}, {}),
    "shutdown": ({}, {}),
    "indicator": ({"on": _is_bool}, {}),
}


def validate_request(request: object) -> str | None:
    """Return a problem description, or ``None`` when the request matches the schema."""
    if not isinstance(request, dict):
        return "request must be a JSON object"
    op = request.get("op")
    if not isinstance(op, str) or op not in _SCHEMA:
        return "unknown or missing op"
    required, optional = _SCHEMA[op]
    for field, check in required.items():
        if field not in request:
            return f"missing field {field!r}"
        if not check(request[field]):
            return f"invalid value for {field!r}"
    for field, value in request.items():
        if field == "op" or field in required:
            continue
        if field not in optional:
            return f"unexpected field {field!r}"
        if not optional[field](value):
            return f"invalid value for {field!r}"
    return None


class ControlServer:
    """Serves control requests on a named pipe, one worker thread per connection."""

    def __init__(self, address: str, key: bytes) -> None:
        """Open the pipe immediately so clients can connect as soon as the key file exists."""
        self.address = address
        self._key = key
        self._stopping = threading.Event()
        self._listener = Listener(address, family="AF_PIPE")

    def serve(self, handler: Handler) -> None:
        """Accept connections until ``stop()`` is called; handler errors never end the loop."""
        try:
            while not self._stopping.is_set():
                try:
                    conn = self._listener.accept()
                except OSError:
                    if self._stopping.is_set():
                        break
                    _log.exception("control accept failed")
                    time.sleep(0.05)
                    continue
                if self._stopping.is_set():
                    conn.close()
                    break
                # Authentication runs in the worker: a half-open client must not stall accept.
                threading.Thread(
                    target=self._serve_connection, args=(conn, handler), daemon=True
                ).start()
        finally:
            self._listener.close()

    def stop(self) -> None:
        """Make ``serve`` return; safe to call from any thread."""
        self._stopping.set()
        # accept() blocks in the OS with no cancel, so wake it with a throwaway connection.
        try:
            _connect(self.address, CONNECT_TIMEOUT_S).close()
        except (OSError, ServiceError):
            pass

    def _serve_connection(self, conn: PipeConnection[Any, Any], handler: Handler) -> None:
        """Authenticate one client, answer one request and close."""
        try:
            try:
                deliver_challenge(conn, self._key)
                answer_challenge(conn, self._key)
            except (AuthenticationError, EOFError, OSError):
                _log.warning("control connection failed authentication")
                return
            if not conn.poll(SERVER_READ_TIMEOUT_S):
                _log.warning("control client sent nothing; dropped")
                return
            conn.send_bytes(json.dumps(self._respond(conn.recv_bytes(), handler)).encode())
        except (EOFError, OSError):
            _log.warning("control connection dropped")
        except Exception:
            _log.exception("control connection failed")
        finally:
            conn.close()

    @staticmethod
    def _respond(raw: bytes, handler: Handler) -> dict[str, Any]:
        """Validate ``raw`` and run the handler, turning every failure into an error reply."""
        try:
            request = json.loads(raw)
        except (ValueError, RecursionError):
            return _bad("request is not valid JSON")
        problem = validate_request(request)
        if problem:
            return _bad(problem)
        try:
            return handler(request)
        except Exception as exc:
            _log.exception("control handler failed for op %s", request["op"])
            return {"ok": False, "error": "internal", "message": f"{type(exc).__name__}: {exc}"}


def _connect(address: str, timeout_s: float) -> PipeConnection[Any, Any]:
    """Open the pipe, retrying while all instances are busy, within ``timeout_s``.

    Raises:
        ServiceNotRunning: If the pipe does not exist or stays busy for the whole budget.
    """
    deadline = time.monotonic() + timeout_s
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
        except OSError as exc:
            if exc.winerror == _ERROR_PIPE_BUSY and time.monotonic() < deadline:
                time.sleep(0.01)
                continue
            raise ServiceNotRunning() from None
        _winapi.SetNamedPipeHandleState(handle, _winapi.PIPE_READMODE_MESSAGE, None, None)
        return PipeConnection(handle)


class ControlClient:
    """Sends single requests to the service; the key may be re-read on every request."""

    def __init__(self, address: str, key: KeySource) -> None:
        """Remember the pipe address and key (or a callable returning the current key)."""
        self.address = address
        self._key = key

    def request(self, message: dict[str, Any]) -> dict[str, Any]:
        """Send ``message`` and return the decoded JSON reply.

        Raises:
            ServiceNotRunning: If nothing listens on the pipe within 1 s.
            ServiceError: On a wrong key, no reply within 5 s, or an unusable reply.
        """
        key = self._key() if callable(self._key) else self._key
        conn = _connect(self.address, CONNECT_TIMEOUT_S)
        result: list[bytes] = []
        failure: list[BaseException] = []

        def exchange() -> None:
            # Run in a thread so a server that never answers cannot hang the CLI: handshake
            # reads have no timeout of their own.
            try:
                answer_challenge(conn, key)
                deliver_challenge(conn, key)
                conn.send_bytes(json.dumps(message).encode())
                if not conn.poll(REPLY_TIMEOUT_S):
                    raise TimeoutError
                result.append(conn.recv_bytes())
            except BaseException as exc:
                failure.append(exc)

        worker = threading.Thread(target=exchange, daemon=True)
        worker.start()
        worker.join(REPLY_TIMEOUT_S + 1.0)
        if worker.is_alive() or (failure and isinstance(failure[0], TimeoutError)):
            raise ServiceError("The camera service did not answer in time")
        conn.close()
        if failure:
            if isinstance(failure[0], AuthenticationError):
                raise ServiceError("The camera service rejected the connection key")
            raise ServiceError(f"Talking to the camera service failed ({failure[0]!r})")
        try:
            reply = json.loads(result[0])
        except ValueError:
            raise ServiceError("The camera service sent an unreadable reply") from None
        if not isinstance(reply, dict):
            raise ServiceError("The camera service sent an unexpected reply")
        return reply
