"""Milestone runner — sequential per-issue execution with state reset."""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from railclaw_pipeline.config import PipelineConfig
from railclaw_pipeline.events.emitter import EventEmitter
from railclaw_pipeline.github.git import GitOperations
from railclaw_pipeline.milestone.collector import collect_milestone_issues, parse_plan_issues
from railclaw_pipeline.prompts.loader import render_template
from railclaw_pipeline.runner.agent import AgentRunner
from railclaw_pipeline.state.models import PipelineState, PipelineStatus
from railclaw_pipeline.state.persistence import save_state

logger = logging.getLogger(__name__)


async def run_milestone(
    milestone_label: str,
    config: PipelineConfig,
    emitter: EventEmitter,
    blueprint_runner: AgentRunner,
    pipeline_func: Any,
) -> list[dict[str, Any]]:
    """Run the full pipeline for all issues in a milestone.

    Steps:
    1. Collect all milestone issues
    2. Run Blueprint holistically for all issues
    3. Parse PLAN.md for execution order
    4. Execute each issue sequentially with state reset

    Args:
        milestone_label: GitHub milestone title.
        config: Pipeline configuration.
        emitter: Event emitter.
        blueprint_runner: Agent runner for Blueprint.
        pipeline_func: The run_pipeline function to call per issue.

    Returns:
        List of result dicts per issue.
    """
    emitter.emit("milestone_start", milestone=milestone_label)

    issues = await collect_milestone_issues(config.repo_path, milestone_label)
    if not issues:
        emitter.emit("milestone_empty", milestone=milestone_label)
        return []

    issue_summaries = [
        {"number": i.get("number"), "title": i.get("title", ""), "body": i.get("body", "")}
        for i in issues
    ]

    emitter.emit("milestone_issues_collected", milestone=milestone_label, count=len(issues))

    context = {
        "milestone": milestone_label,
        "issues": issue_summaries,
        "issue_count": len(issues),
        "repo_path": str(config.repo_path),
    }

    prompt = render_template(config.factory_path, "blueprint.j2", context)
    if not prompt:
        prompt = _build_milestone_blueprint_prompt(milestone_label, issue_summaries)

    emitter.emit("agent_start", issue=0, agent="blueprint", cli="opencode", milestone=milestone_label)
    result = await blueprint_runner.run(prompt)
    emitter.emit(
        "agent_end",
        issue=0,
        agent="blueprint",
        cli="opencode",
        duration_s=result.duration,
        success=result.success,
        milestone=milestone_label,
    )

    if not result.success:
        raise RuntimeError(
            f"Milestone Blueprint failed: {result.error or result.stderr[:500]}"
        )

    plan_path = config.factory_path / "PLAN.md"
    issue_numbers = parse_plan_issues(plan_path)

    if not issue_numbers:
        issue_numbers = [i["number"] for i in issue_summaries]
        logger.warning(
            "No execution order found in PLAN.md, using collection order: %s",
            issue_numbers,
        )

    results = []
    for idx, issue_num in enumerate(issue_numbers):
        emitter.emit(
            "milestone_issue_start",
            milestone=milestone_label,
            issue=issue_num,
            index=idx + 1,
            total=len(issue_numbers),
        )

        state = PipelineState(
            issue_number=issue_num,
            milestone_mode=True,
            milestone_label=milestone_label,
            repo_path=str(config.repo_path),
            timestamps=__import__("railclaw_pipeline.state.models", fromlist=["Timestamps"]).Timestamps(
                started=datetime.now(timezone.utc),
                stage_entered=datetime.now(timezone.utc),
                last_updated=datetime.now(timezone.utc),
            ),
        )
        save_state(state, config.state_path)

        try:
            await pipeline_func(state, config, emitter)
            results.append({
                "issue": issue_num,
                "status": "success",
            })
        except Exception as exc:
            logger.error(
                "Milestone issue #%d failed: %s",
                issue_num,
                exc,
                exc_info=True,
            )
            results.append({
                "issue": issue_num,
                "status": "failed",
                "error": str(exc),
            })
            emitter.emit(
                "milestone_issue_failed",
                milestone=milestone_label,
                issue=issue_num,
                error=str(exc),
            )

        if idx < len(issue_numbers) - 1:
            git_ops = GitOperations(config.repo_path)
            try:
                await git_ops.checkout("main")
                await git_ops.pull("origin", "main")
            except Exception:
                logger.warning("Failed to reset to main between milestone issues", exc_info=True)

    success_count = sum(1 for r in results if r["status"] == "success")
    emitter.emit(
        "milestone_complete",
        milestone=milestone_label,
        total=len(issue_numbers),
        succeeded=success_count,
        failed=len(issue_numbers) - success_count,
    )

    return results


def _build_milestone_blueprint_prompt(milestone: str, issues: list[dict]) -> str:
    issues_text = "\n".join(
        f"  - #{i['number']}: {i['title']}"
        for i in issues
    )
    return (
        "You are Blueprint, the planning agent.\n\n"
        f"Create a holistic plan for milestone '{milestone}' covering {len(issues)} issues:\n"
        f"{issues_text}\n\n"
        "Produce a single PLAN.md with:\n"
        "1. Overall milestone goal\n"
        "2. Execution order (with issue numbers)\n"
        "3. Per-issue phase breakdowns\n"
        "4. Cross-issue dependencies\n\n"
        "Format the execution order as:\n"
        "## Execution Order\n"
        "1. #N — Issue title\n"
        "2. #M — Issue title\n\n"
        "Before starting, read factory/AGENT-RULES.md.\n\n"
        "RESULT_START\nstatus: success\nRESULT_END"
    )
