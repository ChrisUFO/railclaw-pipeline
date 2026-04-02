"""Tests for Stage 0 Preflight — environment readiness checks."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from railclaw_pipeline.config import PipelineConfig
from railclaw_pipeline.events.emitter import EventEmitter
from railclaw_pipeline.state.models import PipelineState, PipelineStage
from railclaw_pipeline.stages.stage0_preflight import PreflightError, run_preflight


@pytest.fixture
def repo_dir(tmp_path):
    return tmp_path / "repo"


@pytest.fixture
def factory_dir(tmp_path):
    return tmp_path / "factory"


@pytest.fixture
def config(repo_dir, factory_dir):
    factory_dir.mkdir(parents=True, exist_ok=True)
    return PipelineConfig({
        "repoPath": str(repo_dir),
        "factoryPath": str(factory_dir),
    })


@pytest.fixture
def emitter(tmp_path):
    return EventEmitter(tmp_path / "events.jsonl")


@pytest.fixture
def state():
    return PipelineState(
        issue_number=42,
        stage=PipelineStage.STAGE0_PREFLIGHT,
        timestamps=__import__("railclaw_pipeline.state.models", fromlist=["Timestamps"]).Timestamps(
            started=datetime.now(timezone.utc),
            stage_entered=datetime.now(timezone.utc),
            last_updated=datetime.now(timezone.utc),
        ),
    )


def test_preflight_error_is_exception():
    err = PreflightError("test error")
    assert str(err) == "test error"
    assert isinstance(err, Exception)


async def test_preflight_missing_repo(config, emitter, state):
    with pytest.raises(PreflightError, match="does not exist"):
        await run_preflight(state, config, emitter)


async def test_preflight_wrong_branch(repo_dir, config, emitter, state):
    repo_dir.mkdir(parents=True)
    (repo_dir / ".git").mkdir()

    with patch("railclaw_pipeline.stages.stage0_preflight.GitOperations") as MockGit:
        mock_git = MockGit.return_value
        mock_git.current_branch = AsyncMock(return_value="develop")
        with pytest.raises(PreflightError, match="Not on main branch"):
            await run_preflight(state, config, emitter)


async def test_preflight_dirty_tree(repo_dir, config, emitter, state):
    repo_dir.mkdir(parents=True)
    (repo_dir / ".git").mkdir()

    with patch("railclaw_pipeline.stages.stage0_preflight.GitOperations") as MockGit:
        mock_git = MockGit.return_value
        mock_git.current_branch = AsyncMock(return_value="main")
        mock_git.is_dirty = AsyncMock(return_value=True)
        with pytest.raises(PreflightError, match="not clean"):
            await run_preflight(state, config, emitter)


async def test_preflight_missing_factory(repo_dir, emitter, state):
    config = PipelineConfig({
        "repoPath": str(repo_dir),
        "factoryPath": str(repo_dir / "nonexistent_factory"),
    })
    repo_dir.mkdir(parents=True)
    (repo_dir / ".git").mkdir()

    with patch("railclaw_pipeline.stages.stage0_preflight.GitOperations") as MockGit:
        mock_git = MockGit.return_value
        mock_git.current_branch = AsyncMock(return_value="main")
        mock_git.is_dirty = AsyncMock(return_value=False)
        mock_git.fetch = AsyncMock(return_value="")
        mock_git.pull = AsyncMock(return_value="")
        with patch("asyncio.create_subprocess_exec") as mock_proc:
            mock_p = AsyncMock()
            mock_p.communicate = AsyncMock(return_value=(b"", b""))
            mock_p.returncode = 0
            mock_proc.return_value = mock_p
            with pytest.raises(PreflightError, match="factory"):
                await run_preflight(state, config, emitter)
