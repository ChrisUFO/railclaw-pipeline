"""Main pipeline runner — orchestrates stage execution."""

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Awaitable

from railclaw_pipeline.config import PipelineConfig
from railclaw_pipeline.events.emitter import EventEmitter
from railclaw_pipeline.github.board import update_issue_status
from railclaw_pipeline.github.checkpoint import (
    archive_checkpoint,
    update_checkpoint,
)
from railclaw_pipeline.runner.agent import AgentRunner
from railclaw_pipeline.runner.agent_config import get_agent_config
from railclaw_pipeline.state.models import PipelineStage, PipelineState, PipelineStatus
from railclaw_pipeline.state.persistence import save_state

logger = logging.getLogger(__name__)


class FatalPipelineError(Exception):
    """Unrecoverable pipeline error — sets status to failed."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


STAGE_TIMEOUTS: dict[str, float] = {
    "stage0_preflight": 120,
    "stage1_blueprint": 600,
    "stage2_wrench": 7200,
    "stage2.5_create_pr": 60,
    "stage3_audit": 300,
    "stage3.5_audit_fix": 600,
    "stage4_review": 300,
    "stage5_fix_loop": 600,
    "cycle2_gemini_loop": 1200,
    "stage7_docs": 600,
    "stage8_approval": 86400,
    "stage8c_merge": 120,
    "stage9_deploy": 300,
    "stage10_qa": 600,
    "stage11_hotfix": 1800,
    "stage12_lessons": 120,
}

STAGE_PHASE_MAP: dict[str, tuple[str, str]] = {
    "stage0_preflight": ("blueprint", "planning"),
    "stage1_blueprint": ("blueprint", "in-progress"),
    "stage2_wrench": ("coding", "in-progress"),
    "stage2.5_create_pr": ("pr-creation", "in-progress"),
    "stage3_audit": ("audit", "in-review"),
    "stage3.5_audit_fix": ("fix", "in-review"),
    "stage4_review": ("review", "in-review"),
    "stage5_fix_loop": ("fix", "in-review"),
    "cycle2_gemini_loop": ("review", "in-review"),
    "stage7_docs": ("docs", "in-progress"),
    "stage8c_merge": ("merge", "merged"),
    "stage9_deploy": ("merge", "merged"),
    "stage10_qa": ("qa", "in-progress"),
    "stage11_hotfix": ("hotfix", "in-progress"),
    "stage12_lessons": ("merge", "closed"),
}


async def run_stage(
    name: str,
    handler: Callable[..., Awaitable[PipelineState]],
    state: PipelineState,
    config: PipelineConfig,
    emitter: EventEmitter,
    *args: Any,
) -> PipelineState:
    """Execute a pipeline stage with timeout, event emission, and state persistence."""
    start = time.monotonic()
    state.stage = PipelineStage(name)
    state.status = PipelineStatus.RUNNING
    if state.timestamps:
        state.timestamps.stage_entered = datetime.now(timezone.utc)
    save_state(state, config.state_path)

    emitter.emit("stage_start", issue=state.issue_number, stage=name)
    update_checkpoint(
        config.factory_path, stage=name, status="running", issue_number=state.issue_number
    )

    timeout = STAGE_TIMEOUTS.get(name)
    try:
        state = await asyncio.wait_for(
            handler(state, config, emitter, *args),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        duration = time.monotonic() - start
        emitter.emit(
            "stage_end", issue=state.issue_number, stage=name,
            duration_s=duration, payload={"success": False, "timeout": True},
        )
        raise
    except FatalPipelineError:
        raise
    except Exception as exc:
        duration = time.monotonic() - start
        emitter.emit(
            "stage_end", issue=state.issue_number, stage=name,
            duration_s=duration, payload={"success": False, "error": str(exc)},
        )
        raise

    duration = time.monotonic() - start
    emitter.emit(
        "stage_end", issue=state.issue_number, stage=name,
        duration_s=duration, payload={"success": True},
    )

    phase, board_status = STAGE_PHASE_MAP.get(name, ("unknown", "in-progress"))
    try:
        update_issue_status(
            config.factory_path, state.issue_number, board_status, stage=name,
            pr_number=state.pr_number,
        )
    except Exception:
        logger.warning("Board update failed for stage %s", name, exc_info=True)

    if state.timestamps:
        state.timestamps.last_updated = datetime.now(timezone.utc)
    save_state(state, config.state_path)
    return state


async def run_pipeline(
    state: PipelineState,
    config: PipelineConfig,
    emitter: EventEmitter,
    hotfix: bool = False,
) -> None:
    """Execute the full pipeline from current stage to completion."""
    from railclaw_pipeline.stages.stage0_preflight import run_preflight
    from railclaw_pipeline.stages.stage1_blueprint import run_blueprint
    from railclaw_pipeline.stages.stage2_wrench import run_wrench
    from railclaw_pipeline.stages.stage2_5_pr import run_create_pr
    from railclaw_pipeline.stages.stage3_audit import run_audit
    from railclaw_pipeline.stages.stage3_5_fix import run_audit_fix
    from railclaw_pipeline.stages.stage4_review import run_review
    from railclaw_pipeline.stages.stage5_fix_loop import run_fix_loop
    from railclaw_pipeline.stages.cycle2_gemini import run_gemini_loop
    from railclaw_pipeline.stages.stage7_docs import run_docs
    from railclaw_pipeline.stages.stage8_approval import run_approval
    from railclaw_pipeline.stages.stage8c_merge import run_merge
    from railclaw_pipeline.stages.stage9_deploy import run_deploy
    from railclaw_pipeline.stages.stage10_qa import run_qa
    from railclaw_pipeline.stages.stage11_hotfix import run_hotfix
    from railclaw_pipeline.stages.stage12_lessons import run_lessons

    blueprint_config = get_agent_config(config, "blueprint")
    wrench_config = get_agent_config(config, "wrench")
    scope_config = get_agent_config(config, "scope")

    try:
        if hotfix:
            wrench_runner = AgentRunner(get_agent_config(config, "wrench"), config.repo_path)
            scope_runner = AgentRunner(get_agent_config(config, "scope"), config.repo_path)
            state = await run_stage(
                "stage11_hotfix", run_hotfix, state, config, emitter,
                wrench_runner, scope_runner,
            )
            state.status = PipelineStatus.COMPLETED
            save_state(state, config.state_path)
            emitter.emit("pipeline_complete", issue=state.issue_number)
            return

        state = await run_stage("stage0_preflight", run_preflight, state, config, emitter)
        state = await run_stage(
            "stage1_blueprint", run_blueprint, state, config, emitter,
            AgentRunner(blueprint_config, config.repo_path),
        )
        state = await run_stage(
            "stage2_wrench", run_wrench, state, config, emitter,
            AgentRunner(wrench_config, config.repo_path),
        )
        state = await run_stage("stage2.5_create_pr", run_create_pr, state, config, emitter)

        state = await run_stage(
            "stage3_audit", run_audit, state, config, emitter,
            AgentRunner(scope_config, config.repo_path),
        )

        current_findings = state.findings.get("current", [])
        if current_findings:
            state = await run_stage(
                "stage3.5_audit_fix", run_audit_fix, state, config, emitter,
                AgentRunner(wrench_config, config.repo_path),
            )
        else:
            emitter.emit("audit_clean", issue=state.issue_number, stage="stage3_audit")

        for rnd in range(5):
            state.cycle.cycle1_round = rnd
            save_state(state, config.state_path)

            state = await run_stage(
                "stage4_review", run_review, state, config, emitter,
                AgentRunner(scope_config, config.repo_path),
            )

            if state.cycle.scope_verdict == "pass":
                break

            if rnd == 4:
                emitter.emit("escalation", issue=state.issue_number, payload={
                    "type": "fix_loop_exhausted",
                    "round": 5,
                    "message": "Fix loop exhausted 5 rounds. Escalation to Chris.",
                })

            state = await run_stage(
                "stage5_fix_loop", run_fix_loop, state, config, emitter,
                AgentRunner(wrench_config, config.repo_path),
            )

        cycle2_cap = 20
        prev_count = -1
        stall = 0
        while not state.cycle.gemini_clean and state.cycle.cycle2_round < cycle2_cap:
            state = await run_stage(
                "cycle2_gemini_loop", run_gemini_loop, state, config, emitter,
                AgentRunner(scope_config, config.repo_path),
            )
            state.cycle.cycle2_round += 1
            save_state(state, config.state_path)

            cur_count = len(state.findings.get("current", []))
            logger.info(
                "cycle2_round %d: findings=%d, gemini_clean=%s, stall=%d",
                state.cycle.cycle2_round, cur_count, state.cycle.gemini_clean, stall,
            )
            if cur_count >= prev_count:
                stall += 1
            else:
                stall = 0
            prev_count = cur_count

            if stall >= 2:
                emitter.emit("cycle2_not_converging", issue=state.issue_number, payload={
                    "rounds": state.cycle.cycle2_round,
                    "findings": cur_count,
                })
                stall = 0

        if not state.cycle.gemini_clean and state.cycle.cycle2_round >= cycle2_cap:
            raise FatalPipelineError(
                "cycle2_safety_cap",
                f"Gemini review loop did not achieve clean status after {cycle2_cap} rounds",
            )

        state = await run_stage("stage7_docs", run_docs, state, config, emitter)
        state = await run_stage("stage8_approval", run_approval, state, config, emitter)
        state = await run_stage("stage8c_merge", run_merge, state, config, emitter)
        state = await run_stage("stage9_deploy", run_deploy, state, config, emitter)
        state = await run_stage(
            "stage10_qa", run_qa, state, config, emitter,
            AgentRunner(get_agent_config(config, "beaker"), config.repo_path),
        )

        state.status = PipelineStatus.COMPLETED
        save_state(state, config.state_path)
        emitter.emit("pipeline_complete", issue=state.issue_number)

    except FatalPipelineError as exc:
        state.status = PipelineStatus.FAILED
        state.error = {"category": exc.category, "message": str(exc), "stage": state.stage.value}
        save_state(state, config.state_path)
        emitter.emit("fatal_error", issue=state.issue_number, payload={
            "category": exc.category, "message": str(exc),
        })
    except asyncio.TimeoutError:
        state.status = PipelineStatus.FAILED
        state.error = {"category": "timeout", "message": f"Stage {state.stage.value} timed out"}
        save_state(state, config.state_path)
        emitter.emit("fatal_error", issue=state.issue_number, payload={
            "category": "timeout", "message": f"Stage {state.stage.value} timed out",
        })
    except Exception as exc:
        state.status = PipelineStatus.FAILED
        state.error = {"category": "unhandled", "message": str(exc), "stage": state.stage.value}
        save_state(state, config.state_path)
        emitter.emit("fatal_error", issue=state.issue_number, payload={
            "category": "unhandled", "message": str(exc),
        })
    finally:
        from railclaw_pipeline.stages.stage12_lessons import run_lessons
        try:
            await run_lessons(state, config, emitter)
        except Exception as lessons_err:
            logger.warning("Failed to write lessons learned: %s", lessons_err)
        try:
            archive_checkpoint(config.factory_path, state.issue_number)
        except Exception as cp_err:
            logger.warning("Failed to archive checkpoint: %s", cp_err)
