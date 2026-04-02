"""Pipeline state models using Pydantic for validation."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class PipelineStage(StrEnum):
    """Operational stages - where the pipeline is in the workflow."""
    IDLE = "idle"
    STAGE0_PREFLIGHT = "stage0_preflight"
    STAGE1_BLUEPRINT = "stage1_blueprint"
    STAGE2_WRENCH = "stage2_wrench"
    STAGE2_5_CREATE_PR = "stage2.5_create_pr"
    STAGE3_AUDIT = "stage3_audit"
    STAGE3_5_AUDIT_FIX = "stage3.5_audit_fix"
    STAGE4_REVIEW = "stage4_review"
    STAGE5_FIX_LOOP = "stage5_fix_loop"
    CYCLE2_GEMINI_LOOP = "cycle2_gemini_loop"
    STAGE7_DOCS = "stage7_docs"
    STAGE8_APPROVAL = "stage8_approval"
    STAGE8C_MERGE = "stage8c_merge"
    STAGE9_DEPLOY = "stage9_deploy"
    STAGE10_QA = "stage10_qa"
    STAGE11_HOTFIX = "stage11_hotfix"
    STAGE12_LESSONS = "stage12_lessons"


class PipelineStatus(StrEnum):
    """Execution status - how the pipeline is running."""
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class Timestamps(BaseModel):
    """Timestamp tracking for pipeline execution."""
    started: datetime
    stage_entered: datetime
    last_updated: datetime


class CycleState(BaseModel):
    """Cycle tracking for fix loops."""
    cycle1_round: int = 0
    cycle2_round: int = 0
    scope_verdict: str = ""
    gemini_clean: bool = False


class PipelineState(BaseModel):
    """Complete pipeline state."""
    version: int = 1
    issue_number: int
    pr_number: int | None = None
    branch: str | None = None
    stage: PipelineStage = PipelineStage.IDLE
    status: PipelineStatus = PipelineStatus.RUNNING
    milestone_mode: bool = False
    milestone_label: str | None = None
    cycle: CycleState = Field(default_factory=CycleState)
    plan_path: str | None = None
    repo_path: str | None = None
    timestamps: Timestamps | None = None
    gemini_tracking: dict[str, Any] = Field(default_factory=lambda: {"pending_poll": False})
    findings: dict[str, Any] = Field(default_factory=lambda: {"current": [], "history": []})
    error: dict[str, Any] | None = None
    retry_count: int = 0
