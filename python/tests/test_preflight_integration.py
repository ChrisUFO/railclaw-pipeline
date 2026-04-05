"""Integration tests for pre-flight gate with CLI commands."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

from railclaw_pipeline.validation.preflight import PreflightGate


class TestPreflightIntegrationWithCLI:
    async def test_preflight_blocks_run_on_failure(self, tmp_path: Path) -> None:
        """Pre-flight failure should block pipeline run."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        factory = tmp_path / "factory"
        factory.mkdir()
        state_dir = factory / ".pipeline-state"
        state_dir.mkdir()

        gate = PreflightGate(
            repo_path=repo,
            factory_path=factory,
            state_path=state_dir / "state.json",
            lock_path=state_dir / "pipeline.lock",
            agent_commands=[],
        )

        proc = AsyncMock()
        proc.returncode = 1
        proc.communicate = AsyncMock(return_value=(b"", b"not authenticated"))
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await gate.run()

        assert result.passed is False
        assert result.failure_count > 0

    async def test_preflight_passes_for_clean_env(self, tmp_path: Path) -> None:
        """Pre-flight should pass in a clean environment."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        factory = tmp_path / "factory"
        factory.mkdir()
        state_dir = factory / ".pipeline-state"
        state_dir.mkdir()

        gate = PreflightGate(
            repo_path=repo,
            factory_path=factory,
            state_path=state_dir / "state.json",
            lock_path=state_dir / "pipeline.lock",
            agent_commands=[],
        )

        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await gate.run()

        assert result.passed is True
        assert result.failure_count == 0

    async def test_preflight_reports_all_failures_at_once(self, tmp_path: Path) -> None:
        """Pre-flight should report ALL failures, not stop at first."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        factory = tmp_path / "factory"
        factory.mkdir()
        state_dir = factory / ".pipeline-state"
        state_dir.mkdir()

        gate = PreflightGate(
            repo_path=repo,
            factory_path=factory,
            state_path=state_dir / "state.json",
            lock_path=state_dir / "pipeline.lock",
            agent_commands=["missing-cli-1", "missing-cli-2"],
        )

        proc = AsyncMock()
        proc.returncode = 1
        proc.communicate = AsyncMock(return_value=(b"", b"error"))
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await gate.run()

        assert result.passed is False
        assert result.failure_count >= 2

    async def test_preflight_result_to_dict_for_cli_output(self, tmp_path: Path) -> None:
        """Pre-flight result should serialize properly for CLI JSON output."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        factory = tmp_path / "factory"
        factory.mkdir()
        state_dir = factory / ".pipeline-state"
        state_dir.mkdir()

        gate = PreflightGate(
            repo_path=repo,
            factory_path=factory,
            state_path=state_dir / "state.json",
            lock_path=state_dir / "pipeline.lock",
            agent_commands=[],
        )

        proc = AsyncMock()
        proc.returncode = 1
        proc.communicate = AsyncMock(return_value=(b"", b"not authed"))
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await gate.run()

        d = result.to_dict()
        assert "passed" in d
        assert "failure_count" in d
        assert "failures" in d
        assert isinstance(d["failures"], list)
        assert all("check" in f and "message" in f for f in d["failures"])
