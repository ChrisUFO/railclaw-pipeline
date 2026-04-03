"""CLI interface for pipeline orchestrator."""

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import click
from railclaw_pipeline.state.models import (
    CycleState,
    PipelineStage,
    PipelineState,
    PipelineStatus,
    Timestamps,
)
from railclaw_pipeline.state.persistence import load_state, save_state


def get_state_path() -> Path:
    """Get state file path from environment or default."""
    state_dir = os.environ.get("RAILCLAW_STATE_DIR", ".pipeline-state")
    factory_path = os.environ.get("RAILCLAW_FACTORY_PATH", "factory")
    return Path(factory_path) / state_dir / "state.json"


def get_events_path() -> Path:
    """Get events file path from environment or default."""
    events_dir = os.environ.get("RAILCLAW_EVENTS_DIR", ".pipeline-events")
    factory_path = os.environ.get("RAILCLAW_FACTORY_PATH", "factory")
    return Path(factory_path) / events_dir / "events.jsonl"


def output_result(data: dict[str, Any]) -> None:
    """Output JSON result to stdout for TypeScript bridge."""
    print(json.dumps(data, default=str))


@click.group()
def main() -> None:
    """RailClaw Pipeline Orchestrator CLI."""
    pass


@main.command()
@click.option("--repo-path", type=str, help="Absolute path to the target repo")
@click.option("--factory-path", type=str, help="Path to factory config directory")
@click.option("--state-dir", type=str, help="State directory (within the factory) to store state")
@click.option("--issue", type=int, help="Issue number to process")
@click.option("--milestone", type=str, help="Milestone label for multi-issue mode")
@click.option("--hotfix", is_flag=True, help="Run in hotfix mode")
@click.option("--force-stage", type=str, help="Force start at specific stage")
def run(
    repo_path: str | None,
    factory_path: str | None,
    state_dir: str | None,
    issue: int | None,
    milestone: str | None,
    hotfix: bool,
    force_stage: str | None,
) -> None:
    """Start a new pipeline run."""
    if not issue and not milestone:
        output_result({
            "ok": False,
            "action": "run",
            "error": "issue or milestone is required"
        })
        return

    if repo_path:
        os.environ["RAILCLAW_REPO_PATH"] = repo_path
    if factory_path:
        os.environ["RAILCLAW_FACTORY_PATH"] = factory_path
    if state_dir:
        os.environ["RAILCLAW_STATE_DIR"] = state_dir
    state_path = get_state_path()
    
    if state_path.exists():
        output_result({
            "ok": False,
            "action": "run",
            "error": "Pipeline already running. Use 'resume' or 'abort' first."
        })
        return
    
    now = datetime.now(timezone.utc)
    state = PipelineState(
        issue_number=issue or 0,
        milestone_mode=milestone is not None,
        milestone_label=milestone,
        stage=PipelineStage.STAGE0_PREFLIGHT if not force_stage else PipelineStage(force_stage),
        status=PipelineStatus.RUNNING,
        timestamps=Timestamps(
            started=now,
            stage_entered=now,
            last_updated=now,
        ),
        cycle=CycleState(),
    )
    
    save_state(state, state_path)

    from railclaw_pipeline.config import PipelineConfig
    from railclaw_pipeline.events.emitter import EventEmitter
    from railclaw_pipeline.pipeline import run_pipeline

    # CLI args override env vars, env vars override defaults
    effective_repo = repo_path or os.environ.get("RAILCLAW_REPO_PATH", ".")
    effective_factory = factory_path or os.environ.get("RAILCLAW_FACTORY_PATH", "factory")

    config = PipelineConfig({
        "repoPath": effective_repo,
        "factoryPath": effective_factory,
    })
    emitter = EventEmitter(config.events_path)

    try:
        if hotfix:
            asyncio.run(run_pipeline(state, config, emitter, hotfix=True))
        else:
            asyncio.run(run_pipeline(state, config, emitter))

        state = load_state(state_path)
    except Exception as exc:
        try:
            state = load_state(state_path)
        except FileNotFoundError:
            pass
        state.status = PipelineStatus.FAILED
        state.error = {"message": str(exc)}
        save_state(state, state_path)
    finally:
        emitter.close()

    output_result({
        "ok": state.status == PipelineStatus.COMPLETED,
        "action": "run",
        "stage": state.stage.value,
        "status": state.status.value,
        "issueNumber": state.issue_number,
        "prNumber": state.pr_number,
        "branch": state.branch,
        "message": f"Pipeline {state.status.value}" + (f": {state.error['message']}" if state.error else ""),
        "statePath": str(state_path),
        "error": state.error.get("message") if state.error else None,
    })


@main.command()
def status() -> None:
    """Show current pipeline status."""
    state_path = get_state_path()
    try:
        state = load_state(state_path)
    except FileNotFoundError:
        output_result({
            "ok": True,
            "action": "status",
            "message": "No active pipeline",
        })
        return
    
    output_result({
        "ok": True,
        "action": "status",
        "stage": state.stage.value,
        "status": state.status.value,
        "issueNumber": state.issue_number,
        "prNumber": state.pr_number,
        "branch": state.branch,
        "message": f"Pipeline {state.status.value} at {state.stage.value}",
        "statePath": str(state_path),
    })


@main.command()
@click.option("--force-stage", type=str, help="Force resume at specific stage")
def resume(force_stage: str | None) -> None:
    """Resume a paused or interrupted pipeline."""
    state_path = get_state_path()
    try:
        state = load_state(state_path)
    except FileNotFoundError:
        output_result({
            "ok": False,
            "action": "resume",
            "error": "No pipeline state found"
        })
        return
    
    if force_stage:
        state.stage = PipelineStage(force_stage)
    
    state.status = PipelineStatus.RUNNING
    state.timestamps.last_updated = datetime.now(timezone.utc) if state.timestamps else None
    
    save_state(state, state_path)
    
    output_result({
        "ok": True,
        "action": "resume",
        "stage": state.stage.value,
        "status": state.status.value,
        "issueNumber": state.issue_number,
        "message": f"Pipeline resumed at {state.stage.value}",
        "statePath": str(state_path),
    })


@main.command()
def abort() -> None:
    """Abort the current pipeline run."""
    state_path = get_state_path()
    try:
        state = load_state(state_path)
    except FileNotFoundError:
        output_result({
            "ok": False,
            "action": "abort",
            "error": "No pipeline state found"
        })
        return
    
    state.status = PipelineStatus.FAILED
    state.error = {"message": "Aborted by user"}
    
    if state.timestamps:
        state.timestamps.last_updated = datetime.now(timezone.utc)
    
    save_state(state, state_path)
    
    output_result({
        "ok": True,
        "action": "abort",
        "stage": state.stage.value,
        "status": state.status.value,
        "issueNumber": state.issue_number,
        "message": "Pipeline aborted",
        "statePath": str(state_path),
    })


if __name__ == "__main__":
    main()
