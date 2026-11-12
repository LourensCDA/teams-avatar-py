import threading
import time
from pathlib import Path

from teams_avatar.locks import DataLock, ServiceLock


def test_data_lock_serialises_writers(tmp_path: Path) -> None:
    lock_path = tmp_path / "data.lock"
    inside = 0
    max_inside = 0
    guard = threading.Lock()

    def writer() -> None:
        nonlocal inside, max_inside
        for _ in range(5):
            with DataLock(lock_path):
                with guard:
                    inside += 1
                    max_inside = max(max_inside, inside)
                time.sleep(0.005)
                with guard:
                    inside -= 1

    threads = [threading.Thread(target=writer) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert max_inside == 1


def test_service_lock_second_try_fails_immediately(tmp_path: Path) -> None:
    lock_path = tmp_path / "service.lock"
    first = ServiceLock(lock_path)
    second = ServiceLock(lock_path)
    assert first.try_acquire()
    start = time.monotonic()
    assert not second.try_acquire()
    assert time.monotonic() - start < 0.5
    first.release()
    assert second.try_acquire()
    second.release()
