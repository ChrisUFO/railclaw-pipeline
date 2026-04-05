"""Tests for cross-platform StateLock."""

import json
import os
import time
from pathlib import Path

import pytest

from railclaw_pipeline.state.lock import LockInfo, StateLock, StateLockError


class TestLockAcquireRelease:
    def test_acquire_and_release(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "test.lock"
        lock = StateLock(lock_path)
        lock.acquire(agent="test", stage="stage1", run_id="issue-1")
        assert lock.is_held()
        lock.release()
        assert not lock.is_held()

    def test_context_manager(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "test.lock"
        lock = StateLock(lock_path)
        with lock:
            assert lock.is_held()
        assert not lock.is_held()

    def test_acquire_creates_parent_dirs(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "deep" / "nested" / "test.lock"
        lock = StateLock(lock_path)
        lock.acquire()
        assert lock_path.exists()
        lock.release()

    def test_lock_file_is_json(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "test.lock"
        lock = StateLock(lock_path)
        lock.acquire(agent="test-agent", stage="stage2", run_id="issue-42")
        data = json.loads(lock_path.read_text())
        assert data["pid"] == os.getpid()
        assert data["agent"] == "test-agent"
        assert data["stage"] == "stage2"
        assert data["run_id"] == "issue-42"
        assert "timestamp" in data
        lock.release()

    def test_get_info_returns_lock_info(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "test.lock"
        lock = StateLock(lock_path)
        lock.acquire(agent="my-agent", run_id="issue-99")
        info = lock.get_info()
        assert info is not None
        assert info.pid == os.getpid()
        assert info.agent == "my-agent"
        assert info.run_id == "issue-99"
        lock.release()


class TestStaleDetection:
    def test_dead_pid_is_stale(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "test.lock"
        lock = StateLock(lock_path, max_age=3600)
        lock.acquire()
        lock.release()

        lock2 = StateLock(lock_path, max_age=3600)
        lock2.acquire(force=True)
        assert lock2.is_held()
        lock2.release()

    def test_stale_lock_raises_error_without_force(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "test.lock"

        stale_data = json.dumps(
            {
                "pid": os.getpid(),
                "timestamp": "2025-01-01T00:00:00",
                "agent": "stale-process",
                "stage": "stage1",
                "run_id": "issue-1",
            }
        )
        lock_path.write_text(stale_data)
        old_time = time.time() - 10
        os.utime(lock_path, (old_time, old_time))

        lock = StateLock(lock_path, max_age=0.001, timeout=0.5)
        with pytest.raises(StateLockError, match="stale"):
            lock.acquire()

    def test_force_override_removes_stale_lock(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "test.lock"
        lock1 = StateLock(lock_path, max_age=3600)
        lock1.acquire()
        lock1.release()

        lock2 = StateLock(lock_path, max_age=0.001, timeout=0.5)
        lock2.acquire(force=True)
        assert lock2.is_held()
        lock2.release()


class TestConcurrentAccess:
    def test_same_process_reacquire_after_release(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "test.lock"
        lock = StateLock(lock_path)
        lock.acquire()
        lock.release()
        lock.acquire()
        assert lock.is_held()
        lock.release()

    def test_timeout_when_lock_held_by_live_process(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "test.lock"
        lock = StateLock(lock_path, timeout=0.3)
        lock.acquire()

        lock2 = StateLock(lock_path, timeout=0.3)
        with pytest.raises(StateLockError, match="Failed to acquire lock"):
            lock2.acquire()

        lock.release()


class TestAtomicWrites:
    def test_lock_file_written_atomically(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "test.lock"
        lock = StateLock(lock_path)
        lock.acquire()
        assert lock_path.exists()
        data = json.loads(lock_path.read_text())
        assert "pid" in data
        assert "timestamp" in data
        lock.release()

    def test_corrupt_lock_file_treated_as_no_lock(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "test.lock"
        lock_path.write_text("not json {{{")

        lock = StateLock(lock_path)
        lock.acquire()
        assert lock.is_held()
        lock.release()


class TestLockInfo:
    def test_lock_info_from_dict(self) -> None:
        info = LockInfo(pid=123, timestamp="2025-01-01T00:00:00Z", agent="test")
        assert info.pid == 123
        assert info.agent == "test"
        assert info.stage == ""
        assert info.run_id == ""


class TestStateLockError:
    def test_is_exception(self) -> None:
        err = StateLockError("test")
        assert isinstance(err, Exception)
        assert str(err) == "test"
