"""File-based advisory lock with cross-platform PID validation."""

import contextlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from railclaw_pipeline.state.pid import is_pid_alive
from railclaw_pipeline.utils.atomic_write import atomic_write

DEFAULT_LOCK_MAX_AGE = 14400


class StateLockError(Exception):
    """Raised when lock acquisition fails."""

    pass


@dataclass
class LockInfo:
    """Parsed lock file contents."""

    pid: int
    timestamp: str
    agent: str = ""
    stage: str = ""
    run_id: str = ""


class StateLock:
    """Cross-platform file-based advisory lock using PID validation.

    Lock file format (JSON):
    {
        "pid": 12345,
        "timestamp": "2025-01-15T10:30:00Z",
        "agent": "opencode",
        "stage": "stage2_wrench",
        "run_id": "issue-42"
    }
    """

    def __init__(
        self,
        lock_path: Path,
        timeout: float = 10.0,
        max_age: float = DEFAULT_LOCK_MAX_AGE,
    ) -> None:
        self.lock_path = lock_path
        self.timeout = timeout
        self.max_age = max_age
        self._acquired = False

    def acquire(
        self,
        agent: str = "",
        stage: str = "",
        run_id: str = "",
        force: bool = False,
    ) -> None:
        """Acquire exclusive lock with timeout and stale detection.

        Args:
            agent: Agent name that acquired the lock.
            stage: Current pipeline stage.
            run_id: Run identifier (e.g., issue number).
            force: If True, remove stale locks and acquire anyway.

        Raises:
            StateLockError: If lock cannot be acquired.
        """
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)

        start_time = time.monotonic()
        while True:
            if self._try_acquire(agent, stage, run_id):
                self._acquired = True
                return

            if not self.lock_path.exists():
                continue

            if self._is_stale():
                if force:
                    self._remove_lock_file()
                    continue
                raise StateLockError(
                    f"Lock is stale (PID dead or age > {self.max_age}s). "
                    f"Use force=True to override: {self.lock_path}"
                )

            if time.monotonic() - start_time >= self.timeout:
                info = self._read_lock_info()
                if info:
                    raise StateLockError(
                        f"Failed to acquire lock after {self.timeout}s. "
                        f"Held by PID {info.pid} at {info.timestamp}"
                    )
                raise StateLockError(
                    f"Failed to acquire lock after {self.timeout}s: {self.lock_path}"
                )

            time.sleep(0.1)

    def release(self) -> None:
        """Release lock if held."""
        if self._acquired:
            self._remove_lock_file()
            self._acquired = False

    def is_held(self) -> bool:
        """Check if lock is currently held by a live process."""
        if not self.lock_path.exists():
            return False
        info = self._read_lock_info()
        if info is None:
            return False
        return is_pid_alive(info.pid)

    def get_info(self) -> LockInfo | None:
        """Read current lock info, or None if no lock exists."""
        return self._read_lock_info()

    def _write_and_verify(self, content: str) -> bool:
        """Write lock content and verify our PID was persisted.

        Returns True if our PID is confirmed in the lock file.
        """
        if self._atomic_write(content):
            info = self._read_lock_info()
            if info and info.pid == os.getpid():
                return True
        return False

    def _try_acquire(self, agent: str, stage: str, run_id: str) -> bool:
        """Attempt to acquire the lock atomically.

        Returns True if lock was acquired, False if already held.
        Uses post-write PID verification to prevent race conditions.
        If another process overwrites our lock, we return False without
        deleting their valid lock.
        """
        if not self.lock_path.exists():
            content = self._build_lock_content(agent, stage, run_id)
            if self._write_and_verify(content):
                return True

        info = self._read_lock_info()
        if info is None or not is_pid_alive(info.pid):
            content = self._build_lock_content(agent, stage, run_id)
            if self._write_and_verify(content):
                return True

        return False

    def _is_stale(self) -> bool:
        """Check if the current lock is stale (dead PID or too old)."""
        info = self._read_lock_info()
        if info is None:
            return True

        if not is_pid_alive(info.pid):
            return True

        try:
            lock_mtime = os.path.getmtime(self.lock_path)
            lock_age = time.time() - lock_mtime
        except OSError:
            return True

        return lock_age > self.max_age

    def _read_lock_info(self) -> LockInfo | None:
        """Parse lock file into LockInfo, or None if unreadable."""
        try:
            content = self.lock_path.read_text()
            data = json.loads(content)
            return LockInfo(
                pid=int(data["pid"]),
                timestamp=str(data["timestamp"]),
                agent=str(data.get("agent", "")),
                stage=str(data.get("stage", "")),
                run_id=str(data.get("run_id", "")),
            )
        except (OSError, json.JSONDecodeError, KeyError, ValueError):
            return None

    def _build_lock_content(self, agent: str, stage: str, run_id: str) -> str:
        """Build JSON lock file content."""
        from datetime import UTC, datetime

        data: dict[str, Any] = {
            "pid": os.getpid(),
            "timestamp": datetime.now(UTC).isoformat(),
            "agent": agent,
            "stage": stage,
            "run_id": run_id,
        }
        return json.dumps(data, indent=2)

    def _atomic_write(self, content: str) -> bool:
        """Write lock file atomically using shared utility."""
        return atomic_write(self.lock_path, content)

    def _remove_lock_file(self) -> None:
        """Remove the lock file."""
        with contextlib.suppress(FileNotFoundError, OSError):
            self.lock_path.unlink()

    def __enter__(self) -> "StateLock":
        self.acquire()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.release()
