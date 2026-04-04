"""Integration tests for CLI detach flow and lifecycle commands."""

import json
import os
import subprocess
import sys
import time

import pytest


@pytest.fixture
def temp_factory_env(tmp_path):
    """Set up a minimal factory environment for CLI integration tests."""
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

    git_script = (
        f"import os; os.chdir(r'{repo}'); "
        f"import subprocess; "
        f"subprocess.run(['git', 'init', '-b', 'main'], capture_output=True); "
        f"subprocess.run(['git', 'config', 'user.name', 'Test User'], capture_output=True); "
        f"subprocess.run(['git', 'config', 'user.email', 'test@test.com'], capture_output=True); "
        f"subprocess.run(['git', 'commit', '--allow-empty', '-m', 'initial'], capture_output=True)"
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
        "events_dir": events_dir,
        "repo": repo,
        "state_path": state_dir / "state.json",
        "pid_path": state_dir / "pipeline.pid",
        "events_path": events_dir / "events.jsonl",
    }

    if old_factory:
        os.environ["RAILCLAW_FACTORY_PATH"] = old_factory
    if old_state:
        os.environ["RAILCLAW_STATE_DIR"] = old_state
    if old_events:
        os.environ["RAILCLAW_EVENTS_DIR"] = old_events
    if old_repo:
        os.environ["RAILCLAW_REPO_PATH"] = old_repo


class TestDetachRun:
    def test_detach_run_returns_immediately_with_pid(self, temp_factory_env):
        """railclaw-pipeline run --detach returns immediately with pid."""
        proc = subprocess.Popen(
            [sys.executable, "-m", "railclaw_pipeline.cli", "run", "--issue", "1", "--detach"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = proc.communicate(timeout=10)
        output = stdout.decode().strip()

        assert proc.returncode == 0, f"CLI failed: {stderr.decode()}"
        result = json.loads(output)
        assert result["ok"] is True
        assert result["status"] == "started"
        assert "pid" in result
        assert isinstance(result["pid"], int)

    def test_detach_run_creates_pid_file(self, temp_factory_env):
        """railclaw-pipeline run --detach creates pipeline.pid file."""
        proc = subprocess.Popen(
            [sys.executable, "-m", "railclaw_pipeline.cli", "run", "--issue", "1", "--detach"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, _ = proc.communicate(timeout=10)
        result = json.loads(stdout.decode().strip())

        pid_path = temp_factory_env["pid_path"]
        assert pid_path.exists(), "PID file should be created in detach mode"
        pid_content = pid_path.read_text().strip().split("\n")[0]
        assert int(pid_content) == result["pid"]

    def test_detach_run_creates_state_file(self, temp_factory_env):
        """railclaw-pipeline run --detach creates state.json with RUNNING status."""
        proc = subprocess.Popen(
            [sys.executable, "-m", "railclaw_pipeline.cli", "run", "--issue", "1", "--detach"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        proc.communicate(timeout=10)

        state_path = temp_factory_env["state_path"]
        assert state_path.exists(), "state.json should be created"
        state = json.loads(state_path.read_text())
        assert state["status"] == "running"
        assert state["issue_number"] == 1

    def test_status_command_returns_running_state(self, temp_factory_env):
        """railclaw-pipeline status returns current running state."""
        subprocess.run(
            [sys.executable, "-m", "railclaw_pipeline.cli", "run", "--issue", "42", "--detach"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        time.sleep(0.5)

        result = subprocess.run(
            [sys.executable, "-m", "railclaw_pipeline.cli", "status"],
            capture_output=True,
        )
        assert result.returncode == 0
        status = json.loads(result.stdout.decode().strip())
        assert status["ok"] is True
        assert status["issueNumber"] == 42
        assert status["status"] in ("running", "failed")

    def test_abort_command_kills_pipeline_and_updates_state(self, temp_factory_env):
        """railclaw-pipeline abort kills the background process and marks state as failed."""
        subprocess.run(
            [sys.executable, "-m", "railclaw_pipeline.cli", "run", "--issue", "99", "--detach"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.5)

        result = subprocess.run(
            [sys.executable, "-m", "railclaw_pipeline.cli", "abort"],
            capture_output=True,
        )
        assert result.returncode == 0
        status = json.loads(result.stdout.decode().strip())
        assert status["ok"] is True
        assert status["status"] == "failed"

        state_path = temp_factory_env["state_path"]
        state = json.loads(state_path.read_text())
        assert state["status"] == "failed"

    def test_status_shows_no_pipeline_when_idle(self, temp_factory_env):
        """railclaw-pipeline status returns 'no active pipeline' when no state exists."""
        result = subprocess.run(
            [sys.executable, "-m", "railclaw_pipeline.cli", "status"],
            capture_output=True,
        )
        assert result.returncode == 0
        status = json.loads(result.stdout.decode().strip())
        assert status["ok"] is True
        assert "no active" in status["message"].lower()

    def test_run_refuses_to_start_when_already_running(self, temp_factory_env):
        """railclaw-pipeline run refuses to start if pipeline is already running."""
        subprocess.run(
            [sys.executable, "-m", "railclaw_pipeline.cli", "run", "--issue", "1", "--detach"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.5)

        result = subprocess.run(
            [sys.executable, "-m", "railclaw_pipeline.cli", "run", "--issue", "2"],
            capture_output=True,
        )
        output = result.stdout.decode().strip()
        status = json.loads(output)
        assert status["ok"] is False
        assert "error" in status
        assert "already running" in status["error"].lower()
