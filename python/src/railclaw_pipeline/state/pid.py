"""PID file management for detached pipeline daemon."""

import contextlib
import logging
import os
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

if sys.platform == "win32":
    import ctypes

    _kernel32 = ctypes.windll.kernel32
    _STILL_ACTIVE = 259
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    def is_pid_alive(pid: int) -> bool:
        handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        exit_code = ctypes.c_ulong()
        _kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        _kernel32.CloseHandle(handle)
        return exit_code.value == _STILL_ACTIVE

else:

    def is_pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False


def write_pid(pid_path: Path, pid: int) -> None:
    """Write PID to file atomically with a timestamp for stale detection."""
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    content = f"{pid}\n{datetime.now(UTC).isoformat()}\n"
    fd, tmp_path = tempfile.mkstemp(
        dir=str(pid_path.parent),
        suffix=".tmp",
        prefix="pid_",
    )
    try:
        with os.fdopen(fd, "w") as tmp_file:
            tmp_file.write(content)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, str(pid_path))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


def read_pid(pid_path: Path) -> int | None:
    """Read PID from file. Returns None if missing or malformed."""
    if not pid_path.exists():
        return None
    try:
        content = pid_path.read_text().strip()
        first_line = content.split("\n")[0]
        return int(first_line)
    except ValueError:
        logger.warning("PID file %s contains non-integer value", pid_path)
        return None
    except OSError as exc:
        logger.warning("Failed to read PID file %s: %s", pid_path, exc)
        return None


def read_pid_timestamp(pid_path: Path) -> datetime | None:
    """Read the timestamp from a PID file, if present."""
    if not pid_path.exists():
        return None
    try:
        lines = pid_path.read_text().strip().split("\n")
        if len(lines) >= 2:
            return datetime.fromisoformat(lines[1])
    except (ValueError, OSError):
        pass
    return None


def kill_pid(pid: int, timeout: float = 10.0) -> bool:
    import signal

    if sys.platform == "win32":
        if not is_pid_alive(pid):
            return True

        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            return not is_pid_alive(pid)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not is_pid_alive(pid):
                return True
            time.sleep(0.2)

        if is_pid_alive(pid):
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F"],
                    capture_output=True,
                    timeout=10,
                )
                time.sleep(0.5)
            except (OSError, FileNotFoundError, subprocess.TimeoutExpired):
                pass

        return not is_pid_alive(pid)

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    except OSError:
        return False

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_pid_alive(pid):
            return True
        time.sleep(0.2)

    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except OSError:
        return False

    time.sleep(0.5)
    return not is_pid_alive(pid)


def remove_pid(pid_path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        pid_path.unlink()
