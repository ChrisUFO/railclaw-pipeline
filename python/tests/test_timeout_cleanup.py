"""Tests for cleanup after timeout."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from railclaw_pipeline.config import PipelineConfig
from railclaw_pipeline.events.emitter import EventEmitter
from railclaw_pipeline.pipeline import run_stage
from railclaw_pipeline.state.models import (
    CycleState,
    PipelineStage,
    PipelineState,
    PipelineStatus,
    Timestamps,
)
from railclaw_pipeline.validation.circuit_breaker import CircuitBreaker


@pytest.fixture
def config(tmp_path: Path) -> PipelineConfig:
    state_dir = tmp_path / ".pipeline-state"
    state_dir.mkdir()
    events_dir = tmp_path / ".pipeline-events"
    events_dir.mkdir()
    return PipelineConfig({"repoPath": str(tmp_path / "repo"), "factoryPath": str(tmp_path)})


@pytest.fixture
def state() -> PipelineState:
    now = datetime.now(UTC)
    return PipelineState(
        issue_number=42,
        milestone_mode=False,
        milestone_label=None,
        stage=PipelineStage.STAGE1_BLUEPRINT,
        status=PipelineStatus.RUNNING,
        timestamps=Timestamps(started=now, stage_entered=now, last_updated=now),
        cycle=CycleState(),
    )


@pytest.fixture
def emitter(tmp_path: Path) -> EventEmitter:
    events_dir = tmp_path / ".pipeline-events"
    events_dir.mkdir(exist_ok=True)
    return EventEmitter(events_dir / "events.jsonl", run_dir=tmp_path / "runs" / "issue-42")


def _read_events(emitter: EventEmitter) -> list[dict]:
    emitter.flush_now()
    if not emitter.events_path.exists():
        return []
    events = []
    for line in emitter.events_path.read_text().strip().split("\n"):
        if line.strip():
            events.append(json.loads(line))
    return events


class TestTimeoutCleanup:
    async def test_cleanup_on_timeout_updates_state(self, config, state, emitter):
        async def slow_handler(s, c, e):
            await asyncio.sleep(30)
            return s

        with patch("railclaw_pipeline.pipeline.STAGE_TIMEOUTS", {"stage1_blueprint": 0.3}):
            with pytest.raises(TimeoutError):
                await run_stage("stage1_blueprint", slow_handler, state, config, emitter)
        state_path = config.state_path
        assert state_path.exists()
        data = json.loads(state_path.read_text())
        assert data["stage"] == "stage1_blueprint"

    async def test_cleanup_emits_timeout_events(self, config, state, emitter):
        async def slow_handler(s, c, e):
            await asyncio.sleep(30)
            return s

        with patch("railclaw_pipeline.pipeline.STAGE_TIMEOUTS", {"stage1_blueprint": 0.3}):
            with pytest.raises(TimeoutError):
                await run_stage("stage1_blueprint", slow_handler, state, config, emitter)
        events = _read_events(emitter)
        timeout_events = [e for e in events if e.get("type") == "stage_timeout"]
        assert len(timeout_events) >= 1

    async def test_cleanup_records_on_circuit_breaker(self, config, state, emitter):
        cb = CircuitBreaker(
            config.factory_path / ".pipeline-state" / "circuit_breaker.json", threshold=1
        )

        async def slow_handler(s, c, e):
            await asyncio.sleep(30)
            return s

        with patch("railclaw_pipeline.pipeline.STAGE_TIMEOUTS", {"stage1_blueprint": 0.3}):
            with pytest.raises(TimeoutError):
                await run_stage(
                    "stage1_blueprint",
                    slow_handler,
                    state,
                    config,
                    emitter,
                    circuit_breaker=cb,
                    agent_name="scope",
                )
        assert cb.get_consecutive_timeouts("scope") == 1
        assert cb.is_open("scope") is True

    async def test_cleanup_persists_state_atomically(self, config, state, emitter):
        async def slow_handler(s, c, e):
            await asyncio.sleep(30)
            return s

        with patch("railclaw_pipeline.pipeline.STAGE_TIMEOUTS", {"stage1_blueprint": 0.3}):
            with pytest.raises(TimeoutError):
                await run_stage("stage1_blueprint", slow_handler, state, config, emitter)
        state_path = config.state_path
        assert state_path.exists()
        data = json.loads(state_path.read_text())
        assert data["stage"] == "stage1_blueprint"
