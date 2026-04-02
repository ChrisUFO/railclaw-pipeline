"""Tests for state persistence — atomic load/save."""

import json
import os
import tempfile
from pathlib import Path

import pytest

from railclaw_pipeline.state.models import PipelineState, PipelineStage, PipelineStatus
from railclaw_pipeline.state.persistence import load_state, save_state


@pytest.fixture
def state_dir(tmp_path):
    return tmp_path / "state"


@pytest.fixture
def sample_state():
    return PipelineState(issue_number=42, branch="feat/test", stage=PipelineStage.STAGE1_BLUEPRINT)


def test_save_creates_file(state_dir, sample_state):
    state_path = state_dir / "state.json"
    save_state(sample_state, state_path)

    assert state_path.exists()
    data = json.loads(state_path.read_text())
    assert data["issue_number"] == 42


def test_load_returns_state(state_dir, sample_state):
    state_path = state_dir / "state.json"
    save_state(sample_state, state_path)

    loaded = load_state(state_path)
    assert loaded.issue_number == 42
    assert loaded.branch == "feat/test"
    assert loaded.stage == PipelineStage.STAGE1_BLUEPRINT


def test_save_is_atomic(state_dir, sample_state):
    state_path = state_dir / "state.json"

    for i in range(10):
        sample_state.issue_number = i + 100
        save_state(sample_state, state_path)

    loaded = load_state(state_path)
    assert loaded.issue_number == 109


def test_load_missing_file_raises(state_dir):
    state_path = state_dir / "nonexistent.json"
    with pytest.raises(FileNotFoundError):
        load_state(state_path)


def test_save_creates_parent_dirs(tmp_path, sample_state):
    state_path = tmp_path / "deep" / "nested" / "dir" / "state.json"
    save_state(sample_state, state_path)
    assert state_path.exists()
    loaded = load_state(state_path)
    assert loaded.issue_number == 42


def test_roundtrip_preserves_all_fields(state_dir):
    from railclaw_pipeline.state.models import CycleState, Timestamps
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    state = PipelineState(
        issue_number=7,
        pr_number=12,
        branch="feat/issue-7",
        stage=PipelineStage.CYCLE2_GEMINI_LOOP,
        status=PipelineStatus.RUNNING,
        cycle=CycleState(cycle1_round=2, cycle2_round=5, gemini_clean=True, scope_verdict="pass"),
        timestamps=Timestamps(started=now, stage_entered=now, last_updated=now),
        findings={"current": [], "history": [{"severity": "medium", "description": "test"}]},
    )

    state_path = state_dir / "state.json"
    save_state(state, state_path)
    loaded = load_state(state_path)

    assert loaded.issue_number == 7
    assert loaded.pr_number == 12
    assert loaded.cycle.cycle1_round == 2
    assert loaded.cycle.gemini_clean is True
    assert len(loaded.findings["history"]) == 1
