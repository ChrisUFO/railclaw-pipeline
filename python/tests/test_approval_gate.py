"""Tests for approval gate — file protocol."""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from railclaw_pipeline.config import PipelineConfig
from railclaw_pipeline.events.emitter import EventEmitter
from railclaw_pipeline.state.models import PipelineState, PipelineStage, PipelineStatus
from railclaw_pipeline.stages.stage8_approval import run_approval


@pytest.fixture
def factory_dir(tmp_path):
    return tmp_path / "factory"


@pytest.fixture
def config(factory_dir):
    return PipelineConfig({
        "repoPath": str(factory_dir / "repo"),
        "factoryPath": str(factory_dir),
        "stateDir": ".pipeline-state",
    })


@pytest.fixture
def emitter(tmp_path):
    return EventEmitter(tmp_path / "events.jsonl")


@pytest.fixture
def state():
    return PipelineState(
        issue_number=42,
        pr_number=7,
        branch="feat/test",
        stage=PipelineStage.STAGE8_APPROVAL,
        timestamps=__import__("railclaw_pipeline.state.models", fromlist=["Timestamps"]).Timestamps(
            started=datetime.now(timezone.utc),
            stage_entered=datetime.now(timezone.utc),
            last_updated=datetime.now(timezone.utc),
        ),
    )


async def test_approval_approved(factory_dir, config, emitter, state):
    approve_path = factory_dir / "approve-7.json"

    async def create_approval():
        await asyncio.sleep(0.1)
        approve_path.parent.mkdir(parents=True, exist_ok=True)
        approve_path.write_text(json.dumps({"approved": True}))

    with patch("railclaw_pipeline.stages.stage8_approval.APPROVAL_POLL_INTERVAL", 0.05):
        with patch("railclaw_pipeline.stages.stage8_approval.DEFAULT_APPROVAL_TIMEOUT", 5):
            task = asyncio.create_task(create_approval())
            result = await run_approval(state, config, emitter)
            await task

    assert result.status == PipelineStatus.RUNNING


async def test_approval_aborted(factory_dir, config, emitter, state):
    abort_path = factory_dir / "abort-7.json"

    async def create_abort():
        await asyncio.sleep(0.1)
        abort_path.parent.mkdir(parents=True, exist_ok=True)
        abort_path.write_text(json.dumps({"aborted": True}))

    with patch("railclaw_pipeline.stages.stage8_approval.APPROVAL_POLL_INTERVAL", 0.05):
        with patch("railclaw_pipeline.stages.stage8_approval.DEFAULT_APPROVAL_TIMEOUT", 5):
            task = asyncio.create_task(create_abort())
            result = await run_approval(state, config, emitter)
            await task

    assert result.status == PipelineStatus.FAILED
    assert result.error is not None
    assert "aborted" in result.error.get("category", "")


async def test_approval_no_pr_raises(config, emitter):
    state = PipelineState(issue_number=42, pr_number=None)
    with pytest.raises(RuntimeError, match="No PR number"):
        await run_approval(state, config, emitter)
