"""PID file management for detached pipeline daemon."""

import contextlib
import logging
import os
import sys
import time
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
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(pid))


def read_pid(pid_path: Path) -> int | None:
    if not pid_path.exists():
        return None
    try:
        return int(pid_path.read_text().strip())
    except ValueError:
        logger.warning("PID file %s contains non-integer value", pid_path)
        return None
    except OSError as exc:
        logger.warning("Failed to read PID file %s: %s", pid_path, exc)
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
