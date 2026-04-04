"""Integration tests for PID lifecycle — external kill and gateway restart simulation."""

import json
import os
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Integration tests spawn console subprocesses that flash on Windows; "
    "covered by unit tests on this platform",
)


@pytest.fixture
def temp_factory_env(tmp_path):
    """Set up minimal factory environment for PID lifecycle tests."""
    factory = tmp_path / "factory"
    state_dir = factory / ".pipeline-state"
    events_dir = factory / ".pipeline-events"
    repo = tmp_path / "repo"

    repo.mkdir()
    state_dir.mkdir(parents=True)
    events_dir.mkdir(parents=True)

    old_env = {
        "GIT_AUTHOR_NAME": os.environ.get("GIT_AUTHOR_NAME"),
        "GIT_AUTHOR_EMAIL": os.environ.get("GIT_AUTHOR_EMAIL"),
        "GIT_COMMITTER_NAME": os.environ.get("GIT_COMMITTER_NAME"),
        "GIT_COMMITTER_EMAIL": os.environ.get("GIT_COMMITTER_EMAIL"),
    }
    os.environ["GIT_AUTHOR_NAME"] = "Test User"
    os.environ["GIT_AUTHOR_EMAIL"] = "test@test.com"
    os.environ["GIT_COMMITTER_NAME"] = "Test User"
    os.environ["GIT_COMMITTER_EMAIL"] = "test@test.com"

    cf = "creationflags=0x08000000, " if sys.platform == "win32" else ""
    git_script = (
        f"import os; os.chdir(r'{repo}'); "
        f"import subprocess; "
        f"subprocess.run(['git', 'init', '-b', 'main'], capture_output=True, {cf}); "
        f"subprocess.run(['git', 'config', 'user.name', 'Test User'], capture_output=True, {cf}); "
        f"subprocess.run(['git', 'config', 'user.email', 'test@test.com'], capture_output=True, {cf}); "
        f"subprocess.run(['git', 'commit', '--allow-empty', '-m', 'initial'], capture_output=True, {cf})"
    )
    subprocess.run([sys.executable, "-c", git_script], capture_output=True)

    for k, v in old_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

    old_factory = os.environ.get("RAILCLAW_FACTORY_PATH")
    old_state = os.environ.get("RAILCLAW_STATE_DIR")
    old_events = os.environ.get("RAILCLAW_EVENTS_DIR")
    old_repo = os.environ.get("RAILCLAW_REPO_PATH")

    os.environ["RAILCLAW_FACTORY_PATH"] = str(factory)
    os.environ["RAILCLAW_STATE_DIR"] = ".pipeline-state"
    os.environ["RAILCLAW_EVENTS_DIR"] = ".pipeline-events"
    os.environ["RAILCLAW_REPO_PATH"] = str(repo)

    yield {
        "factory": factory,
        "state_dir": state_dir,
        "repo": repo,
        "state_path": state_dir / "state.json",
        "pid_path": state_dir / "pipeline.pid",
    }

    if old_factory:
        os.environ["RAILCLAW_FACTORY_PATH"] = old_factory
    if old_state:
        os.environ["RAILCLAW_STATE_DIR"] = old_state
    if old_events:
        os.environ["RAILCLAW_EVENTS_DIR"] = old_events
    if old_repo:
        os.environ["RAILCLAW_REPO_PATH"] = old_repo


def _is_pid_alive(pid):
    """Cross-platform check if a process is alive using ctypes on Windows."""
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        still_active = 259
        process_query_limited_information = 0x1000
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        exit_code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        kernel32.CloseHandle(handle)
        return exit_code.value == still_active
    else:
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return True
        except OSError:
            return False


def _find_dead_pid():
    """Find a PID that does not exist."""
    import random

    for _ in range(1000):
        fake_pid = random.randint(20000, 50000)
        if not _is_pid_alive(fake_pid):
            return fake_pid
    raise RuntimeError("Could not find a dead PID")


class TestPidCheck:
    def test_pid_check_returns_alive_for_current_process(self):
        """-pid-check returns alive=true for the current Python process."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "railclaw_pipeline.cli",
                "--",
                "-pid-check",
                "--pid",
                str(os.getpid()),
            ],
            capture_output=True,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout.decode().strip())
        assert data["alive"] is True

    def test_pid_check_returns_dead_for_nonexistent_pid(self):
        """-pid-check returns alive=false for a nonexistent PID."""
        fake_pid = _find_dead_pid()

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "railclaw_pipeline.cli",
                "--",
                "-pid-check",
                "--pid",
                str(fake_pid),
            ],
            capture_output=True,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout.decode().strip())
        assert data["alive"] is False


class TestStatusWithDeadPid:
    def test_status_marks_interrupted_when_pid_dead(self, temp_factory_env):
        """status command marks state as interrupted when PID file exists but process is dead."""
        state_path = temp_factory_env["state_path"]
        pid_path = temp_factory_env["pid_path"]

        fake_pid = _find_dead_pid()
        state = {
            "issue_number": 1,
            "stage": "stage1_blueprint",
            "status": "running",
            "pid": fake_pid,
            "timestamps": {
                "started": "2024-01-01T00:00:00Z",
                "stage_entered": "2024-01-01T00:00:00Z",
                "last_updated": "2024-01-01T00:00:00Z",
            },
            "cycle": {
                "cycle1_round": 0,
                "cycle2_round": 0,
                "scope_verdict": "",
                "gemini_clean": False,
            },
            "findings": {"current": [], "history": []},
            "retry_count": 0,
        }
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state))
        pid_path.write_text(str(fake_pid))

        result = subprocess.run(
            [sys.executable, "-m", "railclaw_pipeline.cli", "status"],
            capture_output=True,
        )
        json.loads(result.stdout.decode().strip())

        updated_state = json.loads(state_path.read_text())
        assert updated_state["status"] == "failed", (
            "State should be marked failed when running but PID is dead"
        )
        assert updated_state["error"]["message"] == "Pipeline process died unexpectedly"

    def test_status_running_with_valid_pid(self, temp_factory_env):
        """status command shows running when state is running and PID is alive."""
        state_path = temp_factory_env["state_path"]
        pid_path = temp_factory_env["pid_path"]

        current_pid = os.getpid()
        state = {
            "issue_number": 1,
            "stage": "stage1_blueprint",
            "status": "running",
            "pid": current_pid,
            "timestamps": {
                "started": "2024-01-01T00:00:00Z",
                "stage_entered": "2024-01-01T00:00:00Z",
                "last_updated": "2024-01-01T00:00:00Z",
            },
            "cycle": {
                "cycle1_round": 0,
                "cycle2_round": 0,
                "scope_verdict": "",
                "gemini_clean": False,
            },
            "findings": {"current": [], "history": []},
            "retry_count": 0,
        }
        state_path.write_text(json.dumps(state))
        pid_path.write_text(str(current_pid))

        result = subprocess.run(
            [sys.executable, "-m", "railclaw_pipeline.cli", "status"],
            capture_output=True,
        )
        status = json.loads(result.stdout.decode().strip())
        assert status["status"] == "running"
        assert status["pid"] == current_pid


class TestAbortFlow:
    def test_abort_after_detach_removes_pid_file(self, temp_factory_env):
        """abort removes the PID file after killing the process."""
        subprocess.run(
            [sys.executable, "-m", "railclaw_pipeline.cli", "run", "--issue", "1", "--detach"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.3)

        pid_path = temp_factory_env["pid_path"]
        assert pid_path.exists()

        subprocess.run(
            [sys.executable, "-m", "railclaw_pipeline.cli", "abort"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.3)

        assert not pid_path.exists(), "PID file should be removed after abort"
