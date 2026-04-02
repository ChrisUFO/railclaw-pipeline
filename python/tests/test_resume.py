"""Tests for resume — kill-and-resume scenarios."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from railclaw_pipeline.config import PipelineConfig
from railclaw_pipeline.state.models import (
    CycleState,
    PipelineStage,
    PipelineState,
    PipelineStatus,
    Timestamps,
)
from railclaw_pipeline.state.persistence import load_state, save_state


@pytest.fixture
def state_dir(tmp_path):
    return tmp_path / "state"


@pytest.fixture
def config(tmp_path):
    return PipelineConfig({
        "repoPath": str(tmp_path / "repo"),
        "factoryPath": str(tmp_path / "factory"),
    })


def _make_state(stage: PipelineStage, **kwargs) -> PipelineState:
    now = datetime.now(timezone.utc)
    return PipelineState(
        issue_number=42,
        stage=stage,
        status=PipelineStatus.RUNNING,
        timestamps=Timestamps(started=now, stage_entered=now, last_updated=now),
        **kwargs,
    )


def test_save_and_resume_at_stage2(state_dir):
    state = _make_state(PipelineStage.STAGE2_WRENCH, branch="feat/test")
    state_path = state_dir / "state.json"
    save_state(state, state_path)

    loaded = load_state(state_path)
    assert loaded.stage == PipelineStage.STAGE2_WRENCH
    assert loaded.branch == "feat/test"


def test_resume_preserves_cycle_state(state_dir):
    state = _make_state(
        PipelineStage.STAGE5_FIX_LOOP,
        cycle=CycleState(cycle1_round=2, cycle2_round=0, scope_verdict="revision"),
    )
    state_path = state_dir / "state.json"
    save_state(state, state_path)

    loaded = load_state(state_path)
    assert loaded.cycle.cycle1_round == 2
    assert loaded.cycle.scope_verdict == "revision"


def test_resume_preserves_findings(state_dir):
    findings = {
        "current": [{"severity": "high", "description": "test finding"}],
        "history": [{"severity": "medium", "description": "old finding"}],
    }
    state = _make_state(PipelineStage.STAGE4_REVIEW, findings=findings)
    state_path = state_dir / "state.json"
    save_state(state, state_path)

    loaded = load_state(state_path)
    assert len(loaded.findings["current"]) == 1
    assert len(loaded.findings["history"]) == 1


def test_resume_preserves_pr_number(state_dir):
    state = _make_state(PipelineStage.CYCLE2_GEMINI_LOOP, pr_number=7, branch="feat/test")
    state_path = state_dir / "state.json"
    save_state(state, state_path)

    loaded = load_state(state_path)
    assert loaded.pr_number == 7


def test_resume_after_interrupted_save(state_dir):
    state_path = state_dir / "state.json"
    state = _make_state(PipelineStage.STAGE8_APPROVAL, pr_number=12)
    save_state(state, state_path)

    loaded = load_state(state_path)
    loaded.status = PipelineStatus.PAUSED
    loaded.stage = PipelineStage.STAGE8_APPROVAL
    save_state(loaded, state_path)

    resumed = load_state(state_path)
    assert resumed.status == PipelineStatus.PAUSED
    assert resumed.stage == PipelineStage.STAGE8_APPROVAL
    assert resumed.pr_number == 12


def test_resume_milestone_mode(state_dir):
    state = _make_state(
        PipelineStage.STAGE1_BLUEPRINT,
        milestone_mode=True,
        milestone_label="v2.0",
    )
    state_path = state_dir / "state.json"
    save_state(state, state_path)

    loaded = load_state(state_path)
    assert loaded.milestone_mode is True
    assert loaded.milestone_label == "v2.0"


def test_resume_error_state(state_dir):
    state = _make_state(PipelineStage.STAGE2_WRENCH)
    state.status = PipelineStatus.FAILED
    state.error = {"category": "timeout", "message": "Stage timed out"}
    state_path = state_dir / "state.json"
    save_state(state, state_path)

    loaded = load_state(state_path)
    assert loaded.status == PipelineStatus.FAILED
    assert loaded.error["category"] == "timeout"
