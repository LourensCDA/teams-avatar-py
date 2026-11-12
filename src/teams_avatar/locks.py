"""File locks shared by the CLI and the camera service (research R4, R8).

Both use ``msvcrt.locking`` on byte 0 of a lock file. Windows byte-range locks belong to the
file handle, so two handles conflict even inside one process, which lets tests use threads.
"""

from __future__ import annotations

import msvcrt
import os
import time
from pathlib import Path
from types import TracebackType


def _open(path: Path) -> int:
    """Open (creating if needed) the lock file and return its descriptor."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return os.open(path, os.O_RDWR | os.O_CREAT)


class DataLock:
    """Short exclusive lock around reads-modify-writes of the JSON data files."""

    def __init__(self, path: Path, timeout_s: float = 10.0) -> None:
        """Prepare the lock; it is taken by ``__enter__``."""
        self.path = path
        self.timeout_s = timeout_s
        self._fd: int | None = None

    def __enter__(self) -> DataLock:
        """Wait for the lock (polling) for up to ``timeout_s``.

        Raises:
            TimeoutError: If another holder keeps the lock longer than the timeout.
        """
        fd = _open(self.path)
        deadline = time.monotonic() + self.timeout_s
        while True:
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise TimeoutError(f"{self.path.name} stayed locked") from None
                time.sleep(0.005)
        self._fd = fd
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Release the lock."""
        if self._fd is not None:
            os.lseek(self._fd, 0, os.SEEK_SET)
            msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            os.close(self._fd)
            self._fd = None


class ServiceLock:
    """Single-instance lock held by the camera service for its whole life."""

    def __init__(self, path: Path) -> None:
        """Prepare the lock; nothing is taken until ``try_acquire``."""
        self.path = path
        self._fd: int | None = None

    def try_acquire(self) -> bool:
        """Take the lock if free; return False at once if another holder has it."""
        if self._fd is not None:
            return True
        fd = _open(self.path)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        return True

    def release(self) -> None:
        """Release the lock if held."""
        if self._fd is not None:
            os.lseek(self._fd, 0, os.SEEK_SET)
            msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            os.close(self._fd)
            self._fd = None
