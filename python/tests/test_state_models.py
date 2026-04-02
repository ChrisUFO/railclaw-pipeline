"""Tests for pipeline state models."""

import json
from datetime import datetime, timezone

from railclaw_pipeline.state.models import (
    CycleState,
    PipelineStage,
    PipelineState,
    PipelineStatus,
    Timestamps,
)


def test_pipeline_stage_values():
    assert PipelineStage.IDLE == "idle"
    assert PipelineStage.STAGE0_PREFLIGHT == "stage0_preflight"
    assert PipelineStage.STAGE12_LESSONS == "stage12_lessons"


def test_pipeline_status_values():
    assert PipelineStatus.RUNNING == "running"
    assert PipelineStatus.PAUSED == "paused"
    assert PipelineStatus.COMPLETED == "completed"
    assert PipelineStatus.FAILED == "failed"


def test_pipeline_state_defaults():
    state = PipelineState(issue_number=42)
    assert state.issue_number == 42
    assert state.pr_number is None
    assert state.stage == PipelineStage.IDLE
    assert state.status == PipelineStatus.RUNNING
    assert state.milestone_mode is False
    assert state.cycle.cycle1_round == 0
    assert state.cycle.cycle2_round == 0
    assert state.cycle.scope_verdict == ""
    assert state.cycle.gemini_clean is False
    assert state.error is None


def test_pipeline_state_serialization():
    now = datetime.now(timezone.utc)
    state = PipelineState(
        issue_number=42,
        pr_number=7,
        branch="feat/test",
        stage=PipelineStage.STAGE2_WRENCH,
        status=PipelineStatus.RUNNING,
        timestamps=Timestamps(started=now, stage_entered=now, last_updated=now),
        findings={"current": [{"severity": "high", "description": "test"}], "history": []},
    )

    data = state.model_dump(mode="json")
    assert data["issue_number"] == 42
    assert data["pr_number"] == 7
    assert data["stage"] == "stage2_wrench"
    assert data["findings"]["current"][0]["severity"] == "high"

    restored = PipelineState.model_validate(data)
    assert restored.issue_number == 42
    assert restored.branch == "feat/test"
    assert restored.findings["current"][0]["severity"] == "high"


def test_cycle_state_defaults():
    cycle = CycleState()
    assert cycle.cycle1_round == 0
    assert cycle.gemini_clean is False


def test_pipeline_state_json_roundtrip():
    state = PipelineState(
        issue_number=99,
        stage=PipelineStage.STAGE8_APPROVAL,
        status=PipelineStatus.PAUSED,
        cycle=CycleState(cycle1_round=3, gemini_clean=True),
    )
    json_str = state.model_dump_json()
    restored = PipelineState.model_validate_json(json_str)
    assert restored.issue_number == 99
    assert restored.status == PipelineStatus.PAUSED
    assert restored.cycle.cycle1_round == 3
    assert restored.cycle.gemini_clean is True
