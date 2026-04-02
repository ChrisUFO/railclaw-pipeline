"""Tests for Stage 5 Fix Loop — Wrench fixes Scope review findings."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from railclaw_pipeline.config import PipelineConfig
from railclaw_pipeline.events.emitter import EventEmitter
from railclaw_pipeline.runner.agent import AgentConfig, AgentResult
from railclaw_pipeline.runner.subprocess_runner import AgentVerdict
from railclaw_pipeline.state.models import CycleState, PipelineState, PipelineStage
from railclaw_pipeline.stages.stage5_fix_loop import MAX_FIX_ROUNDS, _format_findings, run_fix_loop


@pytest.fixture
def config(tmp_path):
    return PipelineConfig({
        "repoPath": str(tmp_path / "repo"),
        "factoryPath": str(tmp_path / "factory"),
    })


@pytest.fixture
def emitter(tmp_path):
    return EventEmitter(tmp_path / "events.jsonl")


@pytest.fixture
def state():
    return PipelineState(
        issue_number=42,
        branch="feat/test",
        stage=PipelineStage.STAGE5_FIX_LOOP,
        cycle=CycleState(cycle1_round=0),
        findings={"current": [{"severity": "high", "description": "fix this"}], "history": []},
        timestamps=__import__("railclaw_pipeline.state.models", fromlist=["Timestamps"]).Timestamps(
            started=datetime.now(timezone.utc),
            stage_entered=datetime.now(timezone.utc),
            last_updated=datetime.now(timezone.utc),
        ),
    )


def test_max_fix_rounds():
    assert MAX_FIX_ROUNDS == 5


def test_format_findings():
    findings = [
        {"severity": "high", "description": "Bug found", "category": "correctness"},
        {"severity": "low", "description": "Style issue", "category": "polish"},
    ]
    result = _format_findings(findings)
    assert "HIGH" in result
    assert "Bug found" in result
    assert "LOW" in result
    assert "Style issue" in result


def test_format_findings_empty():
    assert _format_findings([]) == ""


async def test_fix_loop_no_findings(config, emitter):
    state = PipelineState(
        issue_number=42,
        findings={"current": [], "history": []},
    )
    runner = AsyncMock()
    result_state = await run_fix_loop(state, config, emitter, runner)
    runner.run.assert_not_called()


async def test_fix_loop_success(config, emitter, state):
    agent_result = AgentResult(
        agent_name="wrench",
        verdict=AgentVerdict.PASS,
        stdout="RESULT_START\nstatus: success\nRESULT_END",
        duration=5.0,
    )
    runner = AsyncMock()
    runner.run = AsyncMock(return_value=agent_result)

    with patch("railclaw_pipeline.stages.stage5_fix_loop.GitOperations") as MockGit:
        mock_git = MockGit.return_value
        mock_git.reset_hard = AsyncMock(return_value="")
        mock_git.clean = AsyncMock(return_value="")

        result = await run_fix_loop(state, config, emitter, runner)

    assert result.findings["current"] == []
    assert len(result.findings["history"]) == 1
    runner.run.assert_called_once()


async def test_fix_loop_failure_raises(config, emitter, state):
    agent_result = AgentResult(
        agent_name="wrench",
        verdict=AgentVerdict.ERROR,
        error="agent crashed",
        stderr="traceback...",
    )
    runner = AsyncMock()
    runner.run = AsyncMock(return_value=agent_result)

    with patch("railclaw_pipeline.stages.stage5_fix_loop.GitOperations") as MockGit:
        mock_git = MockGit.return_value
        mock_git.reset_hard = AsyncMock(return_value="")
        mock_git.clean = AsyncMock(return_value="")

        with pytest.raises(RuntimeError, match="failed"):
            await run_fix_loop(state, config, emitter, runner)
