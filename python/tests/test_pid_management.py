"""Tests for PID file management."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from railclaw_pipeline.state.pid import (
    is_pid_alive,
    kill_pid,
    read_pid,
    remove_pid,
    write_pid,
)


class TestWriteReadPid:
    def test_write_and_read_pid(self, tmp_path: Path) -> None:
        pid_path = tmp_path / "test.pid"
        write_pid(pid_path, 12345)
        assert read_pid(pid_path) == 12345

    def test_write_creates_parent_dirs(self, tmp_path: Path) -> None:
        pid_path = tmp_path / "deep" / "nested" / "test.pid"
        write_pid(pid_path, 999)
        assert read_pid(pid_path) == 999

    def test_read_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert read_pid(tmp_path / "missing.pid") is None

    def test_read_invalid_content_returns_none(self, tmp_path: Path) -> None:
        pid_path = tmp_path / "bad.pid"
        pid_path.write_text("not a number")
        assert read_pid(pid_path) is None

    def test_overwrite_existing_pid(self, tmp_path: Path) -> None:
        pid_path = tmp_path / "test.pid"
        write_pid(pid_path, 100)
        write_pid(pid_path, 200)
        assert read_pid(pid_path) == 200


class TestIsPidAlive:
    def test_current_process_is_alive(self) -> None:
        assert is_pid_alive(os.getpid()) is True

    def test_nonexistent_pid_is_dead(self) -> None:
        assert is_pid_alive(999999999) is False


class TestKillPid:
    def test_kill_nonexistent_pid_returns_true(self) -> None:
        result = kill_pid(999999999)
        assert result is True

    def test_kill_terminates_child_process(self) -> None:
        cmd = [sys.executable, "-c", "import time; time.sleep(60)"]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pid = proc.pid
        assert is_pid_alive(pid) is True

        result = kill_pid(pid, timeout=5.0)
        assert result is True
        assert is_pid_alive(pid) is False

        proc.wait()


class TestRemovePid:
    def test_remove_existing_file(self, tmp_path: Path) -> None:
        pid_path = tmp_path / "test.pid"
        pid_path.write_text("123")
        remove_pid(pid_path)
        assert not pid_path.exists()

    def test_remove_missing_file_no_error(self, tmp_path: Path) -> None:
        remove_pid(tmp_path / "missing.pid")
