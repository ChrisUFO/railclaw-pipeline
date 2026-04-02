"""Tests for Stage 8c: Merge — merge execution and branch cleanup."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from railclaw_pipeline.config import PipelineConfig
from railclaw_pipeline.events.emitter import EventEmitter
from railclaw_pipeline.github.pr import PrError
from railclaw_pipeline.state.models import PipelineState, PipelineStage
from railclaw_pipeline.stages.stage8c_merge import run_merge


@pytest.fixture
def repo_dir(tmp_path):
    return tmp_path / "repo"


@pytest.fixture
def factory_dir(tmp_path):
    return tmp_path / "factory"


@pytest.fixture
def config(repo_dir, factory_dir):
    factory_dir.mkdir(parents=True, exist_ok=True)
    (factory_dir / ".pipeline-state").mkdir(parents=True, exist_ok=True)
    return PipelineConfig({
        "repoPath": str(repo_dir),
        "factoryPath": str(factory_dir),
    })


@pytest.fixture
def emitter(tmp_path):
    return EventEmitter(tmp_path / "events.jsonl")


def _make_state(pr_number=10, branch="feat/issue-42-add-feature"):
    return PipelineState(
        issue_number=42,
        pr_number=pr_number,
        stage=PipelineStage.STAGE8C_MERGE,
        branch=branch,
        timestamps=__import__("railclaw_pipeline.state.models", fromlist=["Timestamps"]).Timestamps(
            started=datetime.now(timezone.utc),
            stage_entered=datetime.now(timezone.utc),
            last_updated=datetime.now(timezone.utc),
        ),
    )


async def test_merge_no_pr_number(config, emitter):
    state = _make_state(pr_number=None)
    with pytest.raises(RuntimeError, match="No PR number"):
        await run_merge(state, config, emitter)


async def test_merge_not_mergeable_after_retries(config, emitter):
    state = _make_state()

    with (
        patch("railclaw_pipeline.stages.stage8c_merge.PrClient") as MockPr,
        patch("railclaw_pipeline.stages.stage8c_merge.asyncio.sleep", new_callable=AsyncMock),
    ):
        mock_pr = MockPr.return_value
        mock_pr.is_mergeable = AsyncMock(return_value=(False, "DIRTY"))
        with pytest.raises(RuntimeError, match="not mergeable"):
            await run_merge(state, config, emitter)


async def test_merge_success_with_branch_cleanup(config, emitter):
    state = _make_state()

    with (
        patch("railclaw_pipeline.stages.stage8c_merge.PrClient") as MockPr,
        patch("railclaw_pipeline.stages.stage8c_merge.GitOperations") as MockGit,
    ):
        mock_pr = MockPr.return_value
        mock_pr.is_mergeable = AsyncMock(return_value=(True, "CLEAN"))
        mock_pr.merge = AsyncMock(return_value="sha123")

        mock_git = MockGit.return_value
        mock_git.checkout = AsyncMock(return_value="main")
        mock_git.pull = AsyncMock(return_value="Already up to date.")
        mock_git.delete_branch = AsyncMock(return_value=None)
        mock_git.delete_remote_branch = AsyncMock(return_value=None)

        result = await run_merge(state, config, emitter)

    mock_git.delete_branch.assert_called_once_with("feat/issue-42-add-feature", force=True)
    mock_git.delete_remote_branch.assert_called_once_with("feat/issue-42-add-feature")
    assert result.timestamps.stage_entered is not None


async def test_merge_success_branch_cleanup_fails_gracefully(config, emitter):
    state = _make_state()

    with (
        patch("railclaw_pipeline.stages.stage8c_merge.PrClient") as MockPr,
        patch("railclaw_pipeline.stages.stage8c_merge.GitOperations") as MockGit,
    ):
        mock_pr = MockPr.return_value
        mock_pr.is_mergeable = AsyncMock(return_value=(True, "CLEAN"))
        mock_pr.merge = AsyncMock(return_value="sha123")

        mock_git = MockGit.return_value
        mock_git.checkout = AsyncMock(return_value="main")
        mock_git.pull = AsyncMock(return_value="Already up to date.")
        mock_git.delete_branch = AsyncMock(side_effect=Exception("branch not found"))
        mock_git.delete_remote_branch = AsyncMock(side_effect=Exception("remote error"))

        result = await run_merge(state, config, emitter)

    assert result.timestamps.stage_entered is not None


async def test_merge_failure_raises(config, emitter):
    state = _make_state()

    with patch("railclaw_pipeline.stages.stage8c_merge.PrClient") as MockPr:
        mock_pr = MockPr.return_value
        mock_pr.is_mergeable = AsyncMock(return_value=(True, "CLEAN"))
        mock_pr.merge = AsyncMock(side_effect=PrError("merge conflict"))

        with pytest.raises(RuntimeError, match="Failed to merge PR"):
            await run_merge(state, config, emitter)


async def test_merge_becomes_mergeable_after_retry(config, emitter):
    state = _make_state()

    with (
        patch("railclaw_pipeline.stages.stage8c_merge.PrClient") as MockPr,
        patch("railclaw_pipeline.stages.stage8c_merge.GitOperations") as MockGit,
        patch("railclaw_pipeline.stages.stage8c_merge.asyncio.sleep", new_callable=AsyncMock),
    ):
        mock_pr = MockPr.return_value
        mock_pr.is_mergeable = AsyncMock(
            side_effect=[(False, "BLOCKED"), (False, "BLOCKED"), (True, "CLEAN")]
        )
        mock_pr.merge = AsyncMock(return_value="sha456")

        mock_git = MockGit.return_value
        mock_git.checkout = AsyncMock(return_value="main")
        mock_git.pull = AsyncMock(return_value="Already up to date.")
        mock_git.delete_branch = AsyncMock(return_value=None)
        mock_git.delete_remote_branch = AsyncMock(return_value=None)

        result = await run_merge(state, config, emitter)

    assert result.timestamps.stage_entered is not None
