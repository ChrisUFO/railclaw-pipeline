"""Tests for timeout isolation — TimeoutError does not propagate beyond stage boundary."""

import asyncio
import contextlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from railclaw_pipeline.config import PipelineConfig
from railclaw_pipeline.events.emitter import EventEmitter
from railclaw_pipeline.pipeline import FatalPipelineError, run_stage
from railclaw_pipeline.runner.subprocess_runner import (
    SubprocessError,
    run_subprocess,
    run_subprocess_safe,
)
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


class TestTimeoutIsolation:
    async def test_timeout_does_not_propagate_beyond_stage(self, config, state, emitter):
        async def slow_handler(s, c, e):
            await asyncio.sleep(30)
            return s

        with patch("railclaw_pipeline.pipeline.STAGE_TIMEOUTS", {"stage1_blueprint": 0.3}):
            with pytest.raises(TimeoutError):
                await run_stage("stage1_blueprint", slow_handler, state, config, emitter)
        assert state.stage.value == "stage1_blueprint"

    async def test_timeout_emits_stage_timeout_event(self, config, state, emitter):
        async def slow_handler(s, c, e):
            await asyncio.sleep(30)
            return s

        with patch("railclaw_pipeline.pipeline.STAGE_TIMEOUTS", {"stage1_blueprint": 0.3}):
            with pytest.raises(TimeoutError):
                await run_stage("stage1_blueprint", slow_handler, state, config, emitter)
        events = _read_events(emitter)
        timeout_events = [e for e in events if e.get("type") == "stage_timeout"]
        assert len(timeout_events) >= 1
        assert timeout_events[0].get("stage") == "stage1_blueprint"

    async def test_non_timeout_error_propagates(self, config, state, emitter):
        async def failing_handler(s, c, e):
            raise RuntimeError("handler error")

        with pytest.raises(RuntimeError, match="handler error"):
            await run_stage("stage1_blueprint", failing_handler, state, config, emitter)

    async def test_fatal_pipeline_error_propagates(self, config, state, emitter):
        async def fatal_handler(s, c, e):
            raise FatalPipelineError("fatal", "fatal error")

        with pytest.raises(FatalPipelineError):
            await run_stage("stage1_blueprint", fatal_handler, state, config, emitter)

    async def test_successful_stage_records_success_on_circuit_breaker(
        self, config, state, emitter
    ):
        cb = CircuitBreaker(config.factory_path / ".pipeline-state" / "circuit_breaker.json")

        async def quick_handler(s, c, e):
            return s

        await run_stage(
            "stage1_blueprint",
            quick_handler,
            state,
            config,
            emitter,
            circuit_breaker=cb,
            agent_name="wrench",
        )
        assert cb.get_consecutive_timeouts("wrench") == 0

    async def test_timeout_records_on_circuit_breaker(self, config, state, emitter):
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
                    agent_name="wrench",
                )
        assert cb.get_consecutive_timeouts("wrench") == 1
        assert cb.is_open("wrench") is True


class TestSubprocessTimeoutIsolation:
    async def test_subprocess_timeout_returns_error_result(self):
        result = await run_subprocess_safe(
            ["python", "-c", "import time; time.sleep(30)"], timeout=0.5
        )
        assert not result.success
        assert result.returncode == -1

    async def test_subprocess_timeout_does_not_leave_zombie(self):
        start = time.monotonic()
        with contextlib.suppress(SubprocessError):
            await run_subprocess(["python", "-c", "import time; time.sleep(30)"], timeout=0.3)
        assert time.monotonic() - start < 15
