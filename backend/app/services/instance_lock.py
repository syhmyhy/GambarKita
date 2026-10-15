"""Single-instance guard for a storage directory.

Live updates and rate limits are held in process memory, so exactly one server
process may own a storage directory. A second process (a double start, or a
service plus a manual launch) must refuse to start, not work on the same files
at the same time. The OS lock is released automatically if the process dies.

Several apps inside one process (as in the test suite) share the lock.
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

_LOCK_RELATIVE_PATH = ("db", "gambarkita.lock")
_LOCK_OFFSET = 0x7FFF0000  # lock a byte beyond EOF: the file itself stays readable
_registry_lock = threading.Lock()
_held: dict[Path, tuple[int, int]] = {}  # lock path -> (fd, reference count)


class StorageInUseError(RuntimeError):
    pass


def _lock_fd(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt  # noqa: PLC0415

        os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl  # noqa: PLC0415

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_fd(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt  # noqa: PLC0415

        os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl  # noqa: PLC0415

        fcntl.flock(fd, fcntl.LOCK_UN)


class StorageLock:
    def __init__(self, storage_dir: Path) -> None:
        self._storage_dir = Path(storage_dir)
        self._path = self._storage_dir.joinpath(*_LOCK_RELATIVE_PATH).resolve()
        self._acquired = False

    def acquire(self) -> None:
        with _registry_lock:
            if self._acquired:
                return
            held = _held.get(self._path)
            if held is not None:
                _held[self._path] = (held[0], held[1] + 1)
                self._acquired = True
                return
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
            fd = os.open(self._path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o640)
            try:
                _lock_fd(fd)
            except OSError as exc:
                os.close(fd)
                raise StorageInUseError(
                    f"Another GambarKita process is already using {self._storage_dir}. "
                    "Run a single server process per storage directory."
                ) from exc
            _held[self._path] = (fd, 1)
            self._acquired = True

    def release(self) -> None:
        with _registry_lock:
            if not self._acquired:
                return
            self._acquired = False
            fd, count = _held[self._path]
            if count > 1:
                _held[self._path] = (fd, count - 1)
                return
            del _held[self._path]
            try:
                _unlock_fd(fd)
            finally:
                os.close(fd)
