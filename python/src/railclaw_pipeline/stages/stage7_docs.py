"""Stage 7: Quill docs — opt-in documentation updates.

Quill only runs when the issue or PLAN.md references doc-impacting changes.
Advisory/non-blocking — does NOT prevent merge.
"""

import logging
from datetime import UTC, datetime

from railclaw_pipeline.config import PipelineConfig
from railclaw_pipeline.events.emitter import EventEmitter
from railclaw_pipeline.github.git import GitOperations
from railclaw_pipeline.prompts.loader import render_template
from railclaw_pipeline.runner.agent import AgentRunner
from railclaw_pipeline.state.models import PipelineState
from railclaw_pipeline.state.persistence import save_state

logger = logging.getLogger(__name__)

DOCS_INDICATORS = [
    "docs:",
    "documentation",
    "readme",
    "api docs",
    "changelog",
    "architectur",
    "guide",
    "tutorial",
    "inline docs",
    "jsdoc",
    "docstring",
]


async def run_docs(
    state: PipelineState,
    config: PipelineConfig,
    emitter: EventEmitter,
    runner: AgentRunner | None = None,
) -> PipelineState:
    """Stage 7: Quill docs — opt-in, advisory, non-blocking.

    Checks issue body and PLAN.md for doc-impacting keywords.
    If found and runner provided, spawns Quill for doc updates.
    If not found, skips entirely.
    """
    should_run = _should_run_docs(state, config)

    if not should_run:
        emitter.emit("docs_skip", issue=state.issue_number, reason="no_doc_indicators")
        if state.timestamps:
            state.timestamps.last_updated = datetime.now(UTC)
        save_state(state, config.state_path)
        return state

    if runner is None:
        emitter.emit("docs_skip", issue=state.issue_number, reason="no_runner")
        if state.timestamps:
            state.timestamps.last_updated = datetime.now(UTC)
        save_state(state, config.state_path)
        return state

    emitter.emit("docs_start", issue=state.issue_number)

    context = {
        "issue_number": state.issue_number,
        "branch": state.branch or "",
        "pr_number": state.pr_number or "",
        "repo_path": str(config.repo_path),
    }

    prompt = render_template(config.factory_path, "quill_docs.j2", context)
    if not prompt:
        prompt = _build_docs_prompt(state)

    emitter.emit("agent_start", issue=state.issue_number, agent="quill", cli="opencode")
    result = await runner.run(prompt)
    emitter.emit(
        "agent_end",
        issue=state.issue_number,
        agent="quill",
        cli="opencode",
        duration_s=result.duration,
        success=result.success,
    )

    if result.success:
        git_ops = GitOperations(config.repo_path)
        await git_ops.add("README.md", "ARCHITECTURE.md", "docs/", "*.md")
        if await git_ops.is_dirty():
            await git_ops.commit(f"docs: update documentation for issue #{state.issue_number}")
            await git_ops.push()
            emitter.emit("docs_committed", issue=state.issue_number)
    else:
        logger.warning(
            "Quill docs failed for issue #%d (non-blocking): %s",
            state.issue_number,
            result.error or result.stderr[:300],
        )
        emitter.emit("docs_skip", issue=state.issue_number, reason="quill_failed")

    if state.timestamps:
        state.timestamps.last_updated = datetime.now(UTC)
    save_state(state, config.state_path)
    return state


def _should_run_docs(state: PipelineState, config: PipelineConfig) -> bool:
    """Check if docs should run based on issue body and PLAN.md content."""
    plan_path = config.factory_path / "PLAN.md"
    if plan_path.exists():
        plan_text = plan_path.read_text(encoding="utf-8").lower()
        for indicator in DOCS_INDICATORS:
            if indicator in plan_text:
                return True

    return False


def _build_docs_prompt(state: PipelineState) -> str:
    return (
        "You are Quill, the documentation agent.\n\n"
        f"Review and update documentation for issue #{state.issue_number}.\n"
        f"Branch: {state.branch}\n\n"
        "Check README.md, ARCHITECTURE.md, docs/*, and inline documentation.\n"
        "Update or create documentation as needed.\n"
        "Doc-only changes — no code changes, no test changes.\n"
        "Commit prefix MUST be 'docs: <description>'.\n\n"
        "Before starting, read factory/AGENT-RULES.md.\n\n"
        "RESULT_START\nstatus: success\nRESULT_END"
    )
