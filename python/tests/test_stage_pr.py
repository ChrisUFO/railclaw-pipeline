"""Tests for Stage 2.5: PR creation — idempotency and error handling."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from railclaw_pipeline.config import PipelineConfig
from railclaw_pipeline.events.emitter import EventEmitter
from railclaw_pipeline.state.models import PipelineState, PipelineStage
from railclaw_pipeline.stages.stage2_5_pr import run_create_pr


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


@pytest.fixture
def state():
    return PipelineState(
        issue_number=42,
        stage=PipelineStage.STAGE2_5_CREATE_PR,
        branch="feat/issue-42-add-feature",
        timestamps=__import__("railclaw_pipeline.state.models", fromlist=["Timestamps"]).Timestamps(
            started=datetime.now(timezone.utc),
            stage_entered=datetime.now(timezone.utc),
            last_updated=datetime.now(timezone.utc),
        ),
    )


async def test_create_pr_no_branch(config, emitter):
    state = PipelineState(
        issue_number=42,
        stage=PipelineStage.STAGE2_5_CREATE_PR,
        timestamps=__import__("railclaw_pipeline.state.models", fromlist=["Timestamps"]).Timestamps(
            started=datetime.now(timezone.utc),
            stage_entered=datetime.now(timezone.utc),
            last_updated=datetime.now(timezone.utc),
        ),
    )
    with pytest.raises(RuntimeError, match="No branch set"):
        await run_create_pr(state, config, emitter)


async def test_create_pr_already_exists(config, emitter, state):
    existing_pr = {"number": 99, "title": "Existing PR", "state": "OPEN"}

    with patch("railclaw_pipeline.stages.stage2_5_pr.PrClient") as MockPr:
        mock_pr = MockPr.return_value
        mock_pr.find_by_head = AsyncMock(return_value=existing_pr)
        result = await run_create_pr(state, config, emitter)

    assert result.pr_number == 99


async def test_create_pr_success(config, emitter, state):
    with patch("railclaw_pipeline.stages.stage2_5_pr.PrClient") as MockPr:
        mock_pr = MockPr.return_value
        mock_pr.find_by_head = AsyncMock(return_value=None)
        mock_pr.create = AsyncMock(return_value={"pr_number": 7, "url": "https://github.com/test/repo/pull/7"})
        result = await run_create_pr(state, config, emitter)

    assert result.pr_number == 7


async def test_create_pr_parse_url(config, emitter, state):
    with patch("railclaw_pipeline.stages.stage2_5_pr.PrClient") as MockPr:
        mock_pr = MockPr.return_value
        mock_pr.find_by_head = AsyncMock(return_value=None)
        mock_pr.create = AsyncMock(return_value={"url": "https://github.com/test/repo/pull/123"})
        result = await run_create_pr(state, config, emitter)

    assert result.pr_number == 123


async def test_create_pr_no_number_no_url(config, emitter, state):
    with patch("railclaw_pipeline.stages.stage2_5_pr.PrClient") as MockPr:
        mock_pr = MockPr.return_value
        mock_pr.find_by_head = AsyncMock(return_value=None)
        mock_pr.create = AsyncMock(return_value={"url": "https://github.com/test/repo/commits/main"})
        with pytest.raises(RuntimeError, match="no number"):
            await run_create_pr(state, config, emitter)


async def test_create_pr_empty_result(config, emitter, state):
    with patch("railclaw_pipeline.stages.stage2_5_pr.PrClient") as MockPr:
        mock_pr = MockPr.return_value
        mock_pr.find_by_head = AsyncMock(return_value=None)
        mock_pr.create = AsyncMock(return_value={})
        with pytest.raises(RuntimeError, match="no number"):
            await run_create_pr(state, config, emitter)
