"""File-based advisory lock for concurrent access."""

import contextlib
import fcntl
import time
from pathlib import Path
from typing import Any, TextIO


class StateLockError(Exception):
    """Raised when lock acquisition fails."""

    pass


class StateLock:
    """File-based advisory lock using fcntl."""

    def __init__(self, lock_path: Path, timeout: float = 10.0):
        self.lock_path = lock_path
        self.timeout = timeout
        self._lock_file: TextIO | None = None

    def acquire(self) -> None:
        """Acquire exclusive lock with timeout."""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)

        self._lock_file = open(self.lock_path, "w")

        start_time = time.monotonic()
        try:
            while True:
                try:
                    fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return
                except OSError:
                    if time.monotonic() - start_time >= self.timeout:
                        raise StateLockError(
                            f"Failed to acquire lock {self.lock_path} after {self.timeout}s"
                        ) from None
                    time.sleep(0.1)
        except BaseException:
            if self._lock_file:
                with contextlib.suppress(IOError, OSError):
                    self._lock_file.close()
            self._lock_file = None
            raise

    def release(self) -> None:
        """Release lock if held."""
        if self._lock_file is not None:
            try:
                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
                self._lock_file.close()
            except OSError:
                pass
            finally:
                self._lock_file = None

    def __enter__(self) -> "StateLock":
        self.acquire()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.release()
