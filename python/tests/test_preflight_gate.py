"""Tests for pre-flight validation gate."""

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from railclaw_pipeline.validation.preflight import (
    PreflightFailure,
    PreflightGate,
    PreflightResult,
)


@pytest.fixture
def repo_dir(tmp_path: Path) -> Path:
    d = tmp_path / "repo"
    d.mkdir()
    (d / ".git").mkdir()
    return d


@pytest.fixture
def factory_dir(tmp_path: Path) -> Path:
    d = tmp_path / "factory"
    d.mkdir()
    (d / ".pipeline-state").mkdir()
    return d


@pytest.fixture
def state_path(factory_dir: Path) -> Path:
    return factory_dir / ".pipeline-state" / "state.json"


@pytest.fixture
def lock_path(factory_dir: Path) -> Path:
    return factory_dir / ".pipeline-state" / "pipeline.lock"


@pytest.fixture
def gate(repo_dir: Path, factory_dir: Path, state_path: Path, lock_path: Path) -> PreflightGate:
    return PreflightGate(
        repo_path=repo_dir,
        factory_path=factory_dir,
        state_path=state_path,
        lock_path=lock_path,
        agent_commands=[],
    )


class TestPreflightResult:
    def test_passed_with_no_failures(self) -> None:
        result = PreflightResult(passed=True)
        assert result.passed is True
        assert result.failure_count == 0

    def test_failed_with_failures(self) -> None:
        failures = [
            PreflightFailure("check_a", "error a", "fix a"),
            PreflightFailure("check_b", "error b", "fix b"),
        ]
        result = PreflightResult(passed=False, failures=failures)
        assert result.passed is False
        assert result.failure_count == 2

    def test_to_dict(self) -> None:
        failures = [PreflightFailure("gh_auth", "not authed", "run gh auth login")]
        result = PreflightResult(passed=False, failures=failures)
        d = result.to_dict()
        assert d["passed"] is False
        assert d["failure_count"] == 1
        assert d["failures"][0]["check"] == "gh_auth"


class TestPreflightFailure:
    def test_dataclass_fields(self) -> None:
        f = PreflightFailure("test", "msg", "fix")
        assert f.check == "test"
        assert f.message == "msg"
        assert f.suggested_fix == "fix"


class TestPreflightGateChecks:
    async def test_check_gh_auth_missing_binary(self, gate: PreflightGate) -> None:
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            result = await gate._check_gh_auth()
        assert result is not None
        assert result.check == "gh_auth"
        assert "not found" in result.message

    async def test_check_gh_auth_not_authenticated(self, gate: PreflightGate) -> None:
        proc = AsyncMock()
        proc.returncode = 1
        proc.communicate = AsyncMock(return_value=(b"", b"not logged in"))
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await gate._check_gh_auth()
        assert result is not None
        assert "not authenticated" in result.message

    async def test_check_gh_auth_success(self, gate: PreflightGate) -> None:
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"Logged in", b""))
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await gate._check_gh_auth()
        assert result is None

    async def test_check_python_cli_missing(self, gate: PreflightGate) -> None:
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            result = await gate._check_python_cli()
        assert result is not None
        assert result.check == "python_cli"

    async def test_check_python_cli_failure(self, gate: PreflightGate) -> None:
        proc = AsyncMock()
        proc.returncode = 1
        proc.communicate = AsyncMock(return_value=(b"", b"error"))
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await gate._check_python_cli()
        assert result is not None

    async def test_check_python_cli_success(self, gate: PreflightGate) -> None:
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"help output", b""))
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await gate._check_python_cli()
        assert result is None

    async def test_check_agent_clis_missing(self, gate: PreflightGate) -> None:
        gate.agent_commands = ["nonexistent-agent-xyz --version"]
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            result = await gate._check_agent_clis()
        assert result is not None
        assert result.check == "agent_clis"

    async def test_check_agent_clis_success(self, gate: PreflightGate) -> None:
        gate.agent_commands = ["opencode --version"]
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"1.0.0", b""))
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await gate._check_agent_clis()
        assert result is None

    async def test_check_repo_missing_path(
        self, factory_dir: Path, state_path: Path, lock_path: Path
    ) -> None:
        gate = PreflightGate(
            repo_path=Path("/nonexistent/path"),
            factory_path=factory_dir,
            state_path=state_path,
            lock_path=lock_path,
        )
        result = await gate._check_repo()
        assert result is not None
        assert "does not exist" in result.message

    async def test_check_repo_not_git_repo(
        self, tmp_path: Path, factory_dir: Path, state_path: Path, lock_path: Path
    ) -> None:
        not_repo = tmp_path / "not_repo"
        not_repo.mkdir()
        gate = PreflightGate(
            repo_path=not_repo,
            factory_path=factory_dir,
            state_path=state_path,
            lock_path=lock_path,
        )
        result = await gate._check_repo()
        assert result is not None
        assert "not a git repository" in result.message.lower()

    async def test_check_repo_dirty_tree(
        self, repo_dir: Path, factory_dir: Path, state_path: Path, lock_path: Path
    ) -> None:
        gate = PreflightGate(
            repo_path=repo_dir,
            factory_path=factory_dir,
            state_path=state_path,
            lock_path=lock_path,
        )
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(b" M some_file.py\n", b""))
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await gate._check_repo()
        assert result is not None
        assert "uncommitted" in result.message

    async def test_check_repo_clean_tree(
        self, repo_dir: Path, factory_dir: Path, state_path: Path, lock_path: Path
    ) -> None:
        gate = PreflightGate(
            repo_path=repo_dir,
            factory_path=factory_dir,
            state_path=state_path,
            lock_path=lock_path,
        )
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(b"", b""))
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await gate._check_repo()
        assert result is None

    async def test_check_disk_space_low(
        self, factory_dir: Path, state_path: Path, lock_path: Path, repo_dir: Path
    ) -> None:
        gate = PreflightGate(
            repo_path=repo_dir,
            factory_path=factory_dir,
            state_path=state_path,
            lock_path=lock_path,
            disk_space_min_mb=999999999,
            agent_commands=[],
        )
        result = await gate._check_disk_space()
        assert result is not None
        assert result.check == "disk_space"

    async def test_check_disk_space_ok(self, gate: PreflightGate) -> None:
        result = await gate._check_disk_space()
        assert result is None

    async def test_check_state_dir_writable(self, gate: PreflightGate) -> None:
        result = await gate._check_state_dir_writable()
        assert result is None

    async def test_check_state_dir_not_writable(
        self, factory_dir: Path, state_path: Path, lock_path: Path, repo_dir: Path
    ) -> None:
        bad_dir = factory_dir / "no-perm" / ".pipeline-state"
        bad_state = bad_dir / "state.json"
        gate = PreflightGate(
            repo_path=repo_dir,
            factory_path=factory_dir,
            state_path=bad_state,
            lock_path=lock_path,
            agent_commands=[],
        )
        with patch.object(Path, "mkdir", side_effect=PermissionError("denied")):
            result = await gate._check_state_dir_writable()
        assert result is not None
        assert result.check == "state_dir"

    async def test_check_no_active_lock_clean(self, gate: PreflightGate) -> None:
        result = await gate._check_no_active_lock()
        assert result is None

    async def test_check_no_active_lock_held(self, gate: PreflightGate, lock_path: Path) -> None:
        lock_data = json.dumps(
            {
                "pid": os.getpid(),
                "timestamp": "2025-01-01T00:00:00",
                "agent": "test",
                "stage": "stage1",
                "run_id": "issue-1",
            }
        )
        lock_path.write_text(lock_data)

        result = await gate._check_no_active_lock()
        assert result is not None
        assert result.check == "active_lock"


class TestPreflightGateRun:
    async def test_run_all_pass(self, gate: PreflightGate) -> None:
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await gate.run()
        assert result.passed is True
        assert result.failure_count == 0

    async def test_run_reports_all_failures(self, gate: PreflightGate) -> None:
        proc = AsyncMock()
        proc.returncode = 1
        proc.communicate = AsyncMock(return_value=(b"", b"error"))
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await gate.run()
        assert result.passed is False
        assert result.failure_count > 0
