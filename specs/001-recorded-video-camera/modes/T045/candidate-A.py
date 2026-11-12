"""Per-user named-pipe control channel between the CLI and the camera service (R4).

Messages are JSON bytes only (``send_bytes``/``recv_bytes``); ``send``/``recv`` would unpickle,
which would let any process that can connect run code inside the service.
"""

from __future__ import annotations

import _winapi
import getpass
import json
import logging
import os
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

_LOG = logging.getLogger(__name__)

CONNECT_TIMEOUT_S = 1.0
REPLY_TIMEOUT_S = 5.0
# Bound on how long the server waits for a client to send its request after authenticating.
REQUEST_TIMEOUT_S = 5.0
_KEY_BYTES = 32
_PIPE_BUSY_RETRY_S = 0.02
_ERROR_PIPE_BUSY = 231

Handler = Callable[[dict[str, Any]], dict[str, Any]]
KeySource = bytes | Callable[[], bytes]


class ControlChannel(Protocol):
    """Anything that can send one control request and return the reply (real client or fake)."""

    def request(self, message: dict[str, Any]) -> dict[str, Any]:
        """Send ``message`` and return the service's reply."""
        ...


def pipe_name(suffix: str | None = None) -> str:
    """Return the per-user pipe address, with ``-<suffix>`` appended when given."""
    user = re.sub(r"[^A-Za-z0-9_.-]", "_", getpass.getuser()) or "user"
    name = f"\\\\.\\pipe\\teams-avatar-{user}"
    return f"{name}-{suffix}" if suffix else name


def write_key(paths: DataPaths) -> bytes:
    """Generate a fresh random key, store it atomically in ``paths.service_key`` and return it."""
    key = secrets.token_bytes(_KEY_BYTES)
    target = paths.service_key
    temp = target.with_name(target.name + ".tmp")
    temp.write_bytes(key)
    os.replace(temp, target)
    return key


def read_key(paths: DataPaths) -> bytes:
    """Read the key the running service wrote.

    Raises:
        ServiceNotRunning: If the key file does not exist.
    """
    try:
        return paths.service_key.read_bytes()
    except FileNotFoundError:
        raise ServiceNotRunning from None


def _bad_request(message: str) -> dict[str, Any]:
    return {"ok": False, "error": "bad_request", "message": message}


def _is_str(value: object) -> bool:
    return isinstance(value, str)


def _is_bool(value: object) -> bool:
    return isinstance(value, bool)


def _is_quality(value: object) -> bool:
    return value in ("720p", "1080p")


_Check = Callable[[object], bool]

# op -> (required fields, optional fields); each field maps to a value check.
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


def validate_request(message: object) -> str | None:
    """Return a description of what is wrong with ``message``, or None when it is valid."""
    if not isinstance(message, dict):
        return "request must be a JSON object"
    op = message.get("op")
    if not isinstance(op, str) or op not in _SCHEMA:
        return "unknown or missing op"
    required, optional = _SCHEMA[op]
    for field, check in required.items():
        if field not in message:
            return f"missing field {field!r}"
        if not check(message[field]):
            return f"invalid value for {field!r}"
    for field, value in message.items():
        if field == "op":
            continue
        if field not in required and field not in optional:
            return f"unexpected field {field!r}"
        if field in optional and not optional[field](value):
            return f"invalid value for {field!r}"
    return None


def _raw_connect(address: str, timeout: float) -> PipeConnection[Any, Any]:
    """Open the pipe without the multiprocessing 20 s busy-wait, within ``timeout`` seconds."""
    deadline = time.monotonic() + timeout
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
        except FileNotFoundError:
            raise ServiceNotRunning from None
        except OSError as exc:
            if getattr(exc, "winerror", None) != _ERROR_PIPE_BUSY:
                raise ServiceError(f"The control pipe cannot be opened ({exc.strerror})") from exc
            if time.monotonic() >= deadline:
                raise ServiceNotRunning from None
            # The single pending pipe instance is mid-accept; a new one appears within moments.
            time.sleep(_PIPE_BUSY_RETRY_S)
            continue
        _winapi.SetNamedPipeHandleState(handle, _winapi.PIPE_READMODE_MESSAGE, None, None)
        return PipeConnection(handle)


class ControlClient:
    """Sends one request per connection to the camera service."""

    def __init__(self, address: str, key: KeySource) -> None:
        """Remember the pipe address and key (bytes, or a callable re-read at each request)."""
        self._address = address
        self._key = key

    def request(self, message: dict[str, Any]) -> dict[str, Any]:
        """Send ``message`` and return the decoded reply.

        Raises:
            ServiceNotRunning: If nothing listens on the pipe within the connect timeout.
            ServiceError: On a wrong key, no reply in time, or a reply that is not a JSON object.
        """
        key = self._key() if callable(self._key) else self._key
        conn = _raw_connect(self._address, CONNECT_TIMEOUT_S)
        try:
            try:
                answer_challenge(conn, key)
                deliver_challenge(conn, key)
            except (AuthenticationError, EOFError, OSError) as exc:
                raise ServiceError(
                    "The camera service rejected the connection key",
                    'Run "teams-avatar shutdown", then try again.',
                ) from exc
            try:
                conn.send_bytes(json.dumps(message).encode("utf-8"))
                if not conn.poll(REPLY_TIMEOUT_S):
                    raise ServiceError("The camera service did not answer in time")
                raw = conn.recv_bytes()
            except (EOFError, OSError) as exc:
                raise ServiceError("The camera service closed the connection") from exc
        finally:
            conn.close()
        try:
            reply = json.loads(raw)
        except ValueError as exc:
            raise ServiceError("The camera service sent an unreadable reply") from exc
        if not isinstance(reply, dict):
            raise ServiceError("The camera service sent an unreadable reply")
        return reply


class ControlServer:
    """Accepts authenticated JSON requests on the pipe and passes valid ones to a handler."""

    def __init__(self, address: str, key: bytes) -> None:
        """Remember the address and key; the pipe is created when ``serve`` starts."""
        self._address = address
        self._key = key
        self._stopped = threading.Event()
        self._lock = threading.Lock()
        self._conns: set[PipeConnection[Any, Any]] = set()
        # Created here, not in serve(), so the pipe exists as soon as the server does and a
        # client that races the serving thread's start-up never sees "no such pipe".
        # No authkey: Listener.accept would run the handshake inline and stall on a client that
        # connects and says nothing; authentication runs per connection instead.
        self._listener = Listener(address, family="AF_PIPE")

    def serve(self, handler: Handler) -> None:
        """Serve requests until ``stop`` is called, then return.

        Each connection gets its own daemon thread so a silent or slow client cannot block
        others. Authentication runs in that thread too, because the listener's own handshake
        would block the accept loop.
        """
        listener = self._listener
        try:
            while not self._stopped.is_set():
                try:
                    conn = listener.accept()
                except OSError:
                    if self._stopped.is_set():
                        break
                    _LOG.exception("control pipe accept failed")
                    time.sleep(0.1)
                    continue
                if self._stopped.is_set():
                    conn.close()
                    break
                threading.Thread(
                    target=self._handle_connection, args=(conn, handler), daemon=True
                ).start()
        finally:
            listener.close()
            with self._lock:
                conns = list(self._conns)
            for conn in conns:
                try:
                    conn.close()
                except OSError:
                    pass

    def stop(self) -> None:
        """Make ``serve`` return; safe to call from another thread."""
        self._stopped.set()
        # accept() blocks in the kernel; a throwaway connection wakes it so it sees the flag.
        try:
            _raw_connect(self._address, CONNECT_TIMEOUT_S).close()
        except ServiceError:
            pass  # serve() is not listening (never started or already returned).

    def _handle_connection(self, conn: PipeConnection[Any, Any], handler: Handler) -> None:
        with self._lock:
            self._conns.add(conn)
        try:
            try:
                deliver_challenge(conn, self._key)
                answer_challenge(conn, self._key)
            except (AuthenticationError, EOFError, OSError):
                _LOG.warning("control connection failed authentication")
                return
            if not conn.poll(REQUEST_TIMEOUT_S):
                _LOG.warning("control client sent no request")
                return
            reply = self._respond(conn.recv_bytes(), handler)
            conn.send_bytes(json.dumps(reply).encode("utf-8"))
        except (EOFError, OSError):
            _LOG.warning("control connection dropped")
        except Exception:
            _LOG.exception("control connection failed")
        finally:
            with self._lock:
                self._conns.discard(conn)
            try:
                conn.close()
            except OSError:
                pass

    @staticmethod
    def _respond(raw: bytes, handler: Handler) -> dict[str, Any]:
        try:
            message = json.loads(raw)
        except ValueError:
            return _bad_request("request is not valid JSON")
        problem = validate_request(message)
        if problem is not None:
            return _bad_request(problem)
        try:
            return handler(message)
        except Exception as exc:
            _LOG.exception("control handler failed for op %s", message["op"])
            return {"ok": False, "error": "internal", "message": f"{type(exc).__name__}: {exc}"}
