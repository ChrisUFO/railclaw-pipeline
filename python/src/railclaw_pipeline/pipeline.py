"""Main pipeline runner — orchestrates stage execution."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

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


STAGE_ORDER = [
    "stage0_preflight",
    "stage1_blueprint",
    "stage2_wrench",
    "stage2.5_create_pr",
    "stage3_audit",
    "stage3.5_audit_fix",
    "stage4_review",
    "stage5_fix_loop",
    "cycle2_gemini_loop",
    "stage7_docs",
    "stage8_approval",
    "stage8c_merge",
    "stage9_deploy",
    "stage10_qa",
    "stage12_lessons",
]


def _should_skip_stage(current_stage: str, resume_from: str) -> bool:
    """Return True if *current_stage* comes before *resume_from* in STAGE_ORDER."""
    try:
        return STAGE_ORDER.index(current_stage) < STAGE_ORDER.index(resume_from)
    except ValueError:
        return False


def _check_circuit_breaker(
    circuit_breaker: Any,
    agent: str,
    issue_number: int,
    emitter: Any,
) -> bool:
    """Check if circuit breaker is open for the given agent.

    Returns True if the circuit is open (should skip this agent's stage).
    Emits an escalation event when the circuit is open.
    """
    if circuit_breaker and circuit_breaker.is_open(agent):
        emitter.emit(
            "escalation",
            issue=issue_number,
            payload={
                "type": "circuit_breaker_open",
                "agent": agent,
                "message": (
                    f"Circuit breaker open for {agent} agent "
                    f"({circuit_breaker.get_consecutive_timeouts(agent)} consecutive timeouts). "
                    f"Skipping and escalating."
                ),
            },
        )
        return True
    return False


async def run_stage(
    name: str,
    handler: Callable[..., Awaitable[PipelineState]],
    state: PipelineState,
    config: PipelineConfig,
    emitter: EventEmitter,
    *args: Any,
    circuit_breaker: Any = None,
    agent_name: str | None = None,
) -> PipelineState:
    """Execute a pipeline stage with timeout, event emission, and state persistence."""
    start = time.monotonic()
    state.stage = PipelineStage(name)
    state.status = PipelineStatus.RUNNING
    if state.timestamps:
        state.timestamps.stage_entered = datetime.now(UTC)
    save_state(state, config.state_path)

    emitter.emit("stage_start", issue=state.issue_number, stage=name)
    emitter.emit_notification(
        "stage_start",
        issue=state.issue_number,
        stage=name,
        next_stage=name,
    )
    update_checkpoint(
        config.factory_path, stage=name, status="running", issue_number=state.issue_number
    )

    timeout = STAGE_TIMEOUTS.get(name)
    try:
        state = await asyncio.wait_for(
            handler(state, config, emitter, *args),
            timeout=timeout,
        )
    except TimeoutError:
        duration = time.monotonic() - start
        emitter.emit(
            "stage_timeout",
            issue=state.issue_number,
            stage=name,
            duration_s=duration,
            agent=agent_name,
        )
        emitter.emit_notification(
            "stage_end",
            issue=state.issue_number,
            stage=name,
            duration_s=duration,
            verdict="timeout",
        )
        if circuit_breaker and agent_name:
            circuit_breaker.record_timeout(agent_name)
        raise
    except FatalPipelineError:
        raise
    except Exception as exc:
        duration = time.monotonic() - start
        emitter.emit(
            "stage_end",
            issue=state.issue_number,
            stage=name,
            duration_s=duration,
            payload={"success": False, "error": str(exc)},
        )
        emitter.emit_notification(
            "stage_end",
            issue=state.issue_number,
            stage=name,
            duration_s=duration,
            verdict="error",
        )
        raise

    if circuit_breaker and agent_name:
        circuit_breaker.record_success(agent_name)

    duration = time.monotonic() - start
    emitter.emit(
        "stage_end",
        issue=state.issue_number,
        stage=name,
        duration_s=duration,
        payload={"success": True},
    )
    emitter.emit_notification(
        "stage_end",
        issue=state.issue_number,
        stage=name,
        duration_s=duration,
        verdict="pass",
        findings_count=len(state.findings.get("current", [])),
    )

    phase, board_status = STAGE_PHASE_MAP.get(name, ("unknown", "in-progress"))
    try:
        update_issue_status(
            config.factory_path,
            state.issue_number,
            board_status,
            stage=name,
            pr_number=state.pr_number,
        )
    except Exception:
        logger.warning("Board update failed for stage %s", name, exc_info=True)

    if state.timestamps:
        state.timestamps.last_updated = datetime.now(UTC)
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
    from railclaw_pipeline.stages.stage2_5_pr import run_create_pr
    from railclaw_pipeline.stages.stage2_wrench import run_wrench
    from railclaw_pipeline.stages.stage3_5_fix import run_audit_fix
    from railclaw_pipeline.stages.stage3_audit import run_audit
    from railclaw_pipeline.stages.stage7_docs import run_docs
    from railclaw_pipeline.stages.stage8_approval import run_approval
    from railclaw_pipeline.stages.stage8c_merge import run_merge
    from railclaw_pipeline.stages.stage9_deploy import run_deploy
    from railclaw_pipeline.stages.stage10_qa import run_qa
    from railclaw_pipeline.stages.stage11_hotfix import run_hotfix
    from railclaw_pipeline.stages.stage12_lessons import run_lessons
    from railclaw_pipeline.validation.circuit_breaker import CircuitBreaker

    blueprint_config = get_agent_config(config, "blueprint")
    wrench_config = get_agent_config(config, "wrench")
    scope_config = get_agent_config(config, "scope")

    cb_path = config.factory_path / config.state_dir / "circuit_breaker.json"
    circuit_breaker = CircuitBreaker(cb_path)

    resume_from = state.stage.value

    def _cleanup_on_timeout(stage_name: str) -> None:
        """Clean up artifacts after a stage timeout."""
        logger.error("Cleaning up after timeout in %s", stage_name)
        # Update state to reflect timeout failure
        state.status = PipelineStatus.FAILED
        state.error = {
            "category": "timeout",
            "message": f"Stage {stage_name} timed out",
            "stage": stage_name,
        }
        save_state(state, config.state_path)
        emitter.emit(
            "stage_cleanup",
            issue=state.issue_number,
            stage=stage_name,
            payload={"action": "timeout_cleanup"},
        )

    try:
        if hotfix:
            wrench_runner = AgentRunner(get_agent_config(config, "wrench"), config.repo_path)
            scope_runner = AgentRunner(get_agent_config(config, "scope"), config.repo_path)
            state = await run_stage(
                "stage11_hotfix",
                run_hotfix,
                state,
                config,
                emitter,
                wrench_runner,
                scope_runner,
            )
            state.status = PipelineStatus.COMPLETED
            save_state(state, config.state_path)
            emitter.emit("pipeline_complete", issue=state.issue_number)
            return

        if not _should_skip_stage("stage0_preflight", resume_from):
            state = await run_stage("stage0_preflight", run_preflight, state, config, emitter)
        if not _should_skip_stage("stage1_blueprint", resume_from):
            state = await run_stage(
                "stage1_blueprint",
                run_blueprint,
                state,
                config,
                emitter,
                AgentRunner(blueprint_config, config.repo_path),
                circuit_breaker=circuit_breaker,
                agent_name="blueprint",
            )
        if not _should_skip_stage("stage2_wrench", resume_from):
            state = await run_stage(
                "stage2_wrench",
                run_wrench,
                state,
                config,
                emitter,
                AgentRunner(wrench_config, config.repo_path),
                circuit_breaker=circuit_breaker,
                agent_name="wrench",
            )
        if not _should_skip_stage("stage2.5_create_pr", resume_from):
            state = await run_stage("stage2.5_create_pr", run_create_pr, state, config, emitter)

        if not _should_skip_stage("stage3_audit", resume_from):
            state = await run_stage(
                "stage3_audit",
                run_audit,
                state,
                config,
                emitter,
                AgentRunner(scope_config, config.repo_path),
                circuit_breaker=circuit_breaker,
                agent_name="scope",
            )

        if not _should_skip_stage("stage3.5_audit_fix", resume_from):
            current_findings = state.findings.get("current", [])
            if current_findings:
                state = await run_stage(
                    "stage3.5_audit_fix",
                    run_audit_fix,
                    state,
                    config,
                    emitter,
                    AgentRunner(wrench_config, config.repo_path),
                    circuit_breaker=circuit_breaker,
                    agent_name="wrench",
                )
            else:
                emitter.emit("audit_clean", issue=state.issue_number, stage="stage3_audit")

        if not _should_skip_stage("stage5_fix_loop", resume_from):
            state = await _run_cycle1_fix_loop(
                state,
                config,
                emitter,
                wrench_config,
                scope_config,
                circuit_breaker,
            )

        if not _should_skip_stage("cycle2_gemini_loop", resume_from):
            state = await _run_cycle2_gemini(
                state,
                config,
                emitter,
                scope_config,
                circuit_breaker,
            )

        if not _should_skip_stage("stage7_docs", resume_from):
            state = await run_stage("stage7_docs", run_docs, state, config, emitter)
        if not _should_skip_stage("stage8_approval", resume_from):
            state = await run_stage("stage8_approval", run_approval, state, config, emitter)
        if not _should_skip_stage("stage8c_merge", resume_from):
            state = await run_stage("stage8c_merge", run_merge, state, config, emitter)
        if not _should_skip_stage("stage9_deploy", resume_from):
            state = await run_stage("stage9_deploy", run_deploy, state, config, emitter)
        if not _should_skip_stage("stage10_qa", resume_from):
            state = await run_stage(
                "stage10_qa",
                run_qa,
                state,
                config,
                emitter,
                AgentRunner(get_agent_config(config, "beaker"), config.repo_path),
                circuit_breaker=circuit_breaker,
                agent_name="beaker",
            )

        state.status = PipelineStatus.COMPLETED
        save_state(state, config.state_path)
        emitter.emit("pipeline_complete", issue=state.issue_number)

    except FatalPipelineError as exc:
        state.status = PipelineStatus.FAILED
        state.error = {"category": exc.category, "message": str(exc), "stage": state.stage.value}
        save_state(state, config.state_path)
        emitter.emit(
            "fatal_error",
            issue=state.issue_number,
            payload={
                "category": exc.category,
                "message": str(exc),
            },
        )
    except TimeoutError:
        _cleanup_on_timeout(state.stage.value)
        emitter.emit(
            "fatal_error",
            issue=state.issue_number,
            payload={
                "category": "timeout",
                "message": f"Stage {state.stage.value} timed out",
            },
        )
    except Exception as exc:
        state.status = PipelineStatus.FAILED
        state.error = {"category": "unhandled", "message": str(exc), "stage": state.stage.value}
        save_state(state, config.state_path)
        emitter.emit(
            "fatal_error",
            issue=state.issue_number,
            payload={
                "category": "unhandled",
                "message": str(exc),
            },
        )
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


async def _run_cycle1_fix_loop(
    state: PipelineState,
    config: PipelineConfig,
    emitter: EventEmitter,
    wrench_config: Any,
    scope_config: Any,
    circuit_breaker: Any = None,
) -> PipelineState:
    """Run the cycle-1 review/fix loop (up to 5 rounds)."""
    from railclaw_pipeline.stages.stage4_review import run_review
    from railclaw_pipeline.stages.stage5_fix_loop import run_fix_loop

    for rnd in range(state.cycle.cycle1_round, 5):
        state.cycle.cycle1_round = rnd
        save_state(state, config.state_path)

        # Check circuit breaker before scope review
        if _check_circuit_breaker(circuit_breaker, "scope", state.issue_number, emitter):
            break

        state = await run_stage(
            "stage4_review",
            run_review,
            state,
            config,
            emitter,
            AgentRunner(scope_config, config.repo_path),
            circuit_breaker=circuit_breaker,
            agent_name="scope",
        )

        if state.cycle.scope_verdict == "pass":
            break

        # Check circuit breaker before wrench fix
        if _check_circuit_breaker(circuit_breaker, "wrench", state.issue_number, emitter):
            break

        if rnd == 4:
            emitter.emit(
                "escalation",
                issue=state.issue_number,
                payload={
                    "type": "fix_loop_exhausted",
                    "round": 5,
                    "message": "Fix loop exhausted 5 rounds. Escalation to Chris.",
                },
            )

        state = await run_stage(
            "stage5_fix_loop",
            run_fix_loop,
            state,
            config,
            emitter,
            AgentRunner(wrench_config, config.repo_path),
            circuit_breaker=circuit_breaker,
            agent_name="wrench",
        )

    return state


async def _run_cycle2_gemini(
    state: PipelineState,
    config: PipelineConfig,
    emitter: EventEmitter,
    scope_config: Any,
    circuit_breaker: Any = None,
) -> PipelineState:
    """Run the cycle-2 Gemini review loop (up to 20 rounds with stall detection)."""
    from railclaw_pipeline.stages.cycle2_gemini import run_gemini_loop

    cycle2_cap = 20
    prev_count = -1
    stall = 0
    while not state.cycle.gemini_clean and state.cycle.cycle2_round < cycle2_cap:
        # Check circuit breaker before each iteration
        if _check_circuit_breaker(circuit_breaker, "scope", state.issue_number, emitter):
            break

        state = await run_stage(
            "cycle2_gemini_loop",
            run_gemini_loop,
            state,
            config,
            emitter,
            AgentRunner(scope_config, config.repo_path),
            circuit_breaker=circuit_breaker,
            agent_name="scope",
        )
        state.cycle.cycle2_round += 1
        save_state(state, config.state_path)

        cur_count = len(state.findings.get("current", []))
        logger.info(
            "cycle2_round %d: findings=%d, gemini_clean=%s, stall=%d",
            state.cycle.cycle2_round,
            cur_count,
            state.cycle.gemini_clean,
            stall,
        )
        if cur_count >= prev_count:
            stall += 1
        else:
            stall = 0
        prev_count = cur_count

        if stall >= 2:
            emitter.emit(
                "cycle2_not_converging",
                issue=state.issue_number,
                payload={
                    "rounds": state.cycle.cycle2_round,
                    "findings": cur_count,
                },
            )
            stall = 0

    if not state.cycle.gemini_clean and state.cycle.cycle2_round >= cycle2_cap:
        raise FatalPipelineError(
            "cycle2_safety_cap",
            f"Gemini review loop did not achieve clean status after {cycle2_cap} rounds",
        )

    return state
