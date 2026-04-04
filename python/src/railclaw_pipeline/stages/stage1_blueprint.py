"""Stage 1: Blueprint — planning agent produces PLAN.md."""

import logging
from datetime import UTC, datetime
from pathlib import Path

from railclaw_pipeline.config import PipelineConfig
from railclaw_pipeline.events.emitter import EventEmitter
from railclaw_pipeline.github.gh import GhClient
from railclaw_pipeline.github.git import sanitize_branch_name
from railclaw_pipeline.prompts.loader import render_template
from railclaw_pipeline.runner.agent import AgentRunner
from railclaw_pipeline.state.models import PipelineState
from railclaw_pipeline.state.persistence import save_state

logger = logging.getLogger(__name__)


async def run_blueprint(
    state: PipelineState,
    config: PipelineConfig,
    emitter: EventEmitter,
    runner: AgentRunner,
) -> PipelineState:
    """Stage 1: Fetch issue, invoke Blueprint agent, write PLAN.md.

    Blueprint reads the issue, analyzes the codebase, and writes
    a detailed implementation plan to PLAN.md in the repo root.
    """
    gh = GhClient(config.repo_path)
    issue_data = await gh.issue_view(state.issue_number)

    issue_title = issue_data.get("title", "")
    issue_body = issue_data.get("body", "")

    branch_name = sanitize_branch_name(
        f"feat/issue-{state.issue_number}-{_slugify(issue_title)}"
    )
    state.branch = branch_name
    state.plan_path = str(config.repo_path / "PLAN.md")

    context = {
        "issue_number": state.issue_number,
        "issue_title": issue_title,
        "issue_body": issue_body,
        "repo_name": config.repo_path.name,
        "branch": branch_name,
    }

    prompt = render_template(config.factory_path, "blueprint.j2", context)
    if not prompt:
        prompt = _build_blueprint_prompt(state, issue_title, issue_body, branch_name, config)

    emitter.emit("agent_start", issue=state.issue_number, agent="blueprint", cli="opencode")
    result = await runner.run(prompt)
    emitter.emit(
        "agent_end",
        issue=state.issue_number,
        agent="blueprint",
        cli="opencode",
        duration_s=result.duration,
        success=result.success,
    )

    plan_path = Path(state.plan_path) if state.plan_path else config.repo_path / "PLAN.md"
    if not plan_path.exists():
        raise RuntimeError(f"Blueprint did not produce PLAN.md at {plan_path}")

    state.timestamps.stage_entered = datetime.now(UTC)
    save_state(state, config.state_path)
    return state


def _slugify(text: str) -> str:
    """Convert text to a branch-safe slug."""
    import re
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = slug.strip("-")
    slug = slug[:200]
    return slug[:50]


def _build_blueprint_prompt(
    state: PipelineState,
    issue_title: str,
    issue_body: str,
    branch: str,
    config: PipelineConfig,
) -> str:
    """Fallback prompt when template not available."""
    return (
        f"You are Blueprint, the planning agent.\n\n"
        f"Create a detailed PLAN.md for issue #{state.issue_number}: {issue_title}\n\n"
        f"Issue body:\n{issue_body}\n\n"
        f"Repository: {config.repo_path.name}\n"
        f"Branch: {branch}\n\n"
        f"Write the plan to PLAN.md in the repo root.\n"
        f"Include phases with deliverables, tests, and dependencies.\n\n"
        f"RESULT_START\nstatus: success\nRESULT_END"
    )
