"""Stage 2: Wrench — implementation agent executes PLAN.md phases."""

import logging
from datetime import datetime, timezone
from pathlib import Path

from railclaw_pipeline.config import PipelineConfig
from railclaw_pipeline.events.emitter import EventEmitter
from railclaw_pipeline.github.git import GitOperations
from railclaw_pipeline.prompts.loader import render_template
from railclaw_pipeline.runner.agent import AgentConfig, AgentRunner
from railclaw_pipeline.state.models import PipelineState
from railclaw_pipeline.state.persistence import save_state

logger = logging.getLogger(__name__)


async def run_wrench(
    state: PipelineState,
    config: PipelineConfig,
    runner: AgentRunner,
    emitter: EventEmitter,
) -> PipelineState:
    """Stage 2: Wrench implements the plan phase by phase.

    On resume, resets working tree to discard partial work from a crashed run,
    then re-executes the full Wrench prompt against a clean state.
    """
    repo = config.repo_path
    git_ops = GitOperations(repo)

    if not state.branch:
        raise RuntimeError("No branch set — Blueprint must run first")

    await _ensure_branch(git_ops, state.branch)

    await git_ops.reset_hard("HEAD")
    await git_ops.clean()

    plan_path = Path(state.plan_path) if state.plan_path else repo / "PLAN.md"
    plan_content = ""
    if plan_path.exists():
        plan_content = plan_path.read_text(encoding="utf-8")

    if not plan_content:
        raise RuntimeError(f"PLAN.md not found or empty at {plan_path}")

    context = {
        "issue_number": state.issue_number,
        "repo_path": str(repo),
        "branch": state.branch,
        "plan_path": str(plan_path),
    }

    prompt = render_template(config.factory_path, "wrench-implement.j2", context)
    if not prompt:
        prompt = _build_wrench_prompt(state, plan_content, config)

    emitter.emit("agent_start", issue=state.issue_number, agent="wrench", cli="opencode")
    result = await runner.run(prompt)
    emitter.emit(
        "agent_end",
        issue=state.issue_number,
        agent="wrench",
        cli="opencode",
        duration_s=result.duration,
        success=result.success,
    )

    if not result.success:
        raise RuntimeError(
            f"Wrench implementation failed: {result.error or result.stderr[:500]}"
        )

    state.timestamps.stage_entered = datetime.now(timezone.utc)
    save_state(state, config.state_path)
    return state


async def _ensure_branch(git_ops: GitOperations, branch: str) -> None:
    """Create feature branch from main if it doesn't exist."""
    current = await git_ops.current_branch()
    if current == branch:
        return

    exists = await git_ops.branch_exists(branch)
    if exists:
        await git_ops.checkout(branch)
    else:
        await git_ops.checkout_new(branch, "main")


def _build_wrench_prompt(
    state: PipelineState,
    plan_content: str,
    config: PipelineConfig,
) -> str:
    """Fallback prompt when Jinja2 template is not available."""
    return (
        f"You are Wrench, the implementation agent.\n\n"
        f"Implement the following plan for issue #{state.issue_number}.\n\n"
        f"Branch: {state.branch}\n"
        f"Repository: {config.repo_path}\n\n"
        f"PLAN.md:\n{plan_content}\n\n"
        f"Implement ALL phases from the plan. Commit after each phase.\n"
        f"Follow all conventions in the existing codebase.\n"
        f"Write tests for all new code.\n\n"
        f"Before starting, read factory/AGENT-RULES.md and follow all rules defined there.\n\n"
        f"RESULT_START\nstatus: success\nRESULT_END"
    )
