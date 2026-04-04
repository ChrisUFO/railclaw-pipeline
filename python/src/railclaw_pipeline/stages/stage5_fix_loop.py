"""Stage 5: Fix Loop — Wrench fixes Scope review findings, max 5 rounds."""

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

MAX_FIX_ROUNDS = 5


async def run_fix_loop(
    state: PipelineState,
    config: PipelineConfig,
    emitter: EventEmitter,
    runner: AgentRunner,
) -> PipelineState:
    """Stage 5: Wrench fixes review findings.

    Runs up to MAX_FIX_ROUNDS rounds. On resume, resets working tree.
    Escalation at round 3 (Wrench Sr available) and round 5 (Chris mandatory).
    """
    current_findings = state.findings.get("current", [])
    if not current_findings:
        emitter.emit("fix_loop_clean", issue=state.issue_number)
        return state

    round_num = state.cycle.cycle1_round
    logger.info("Fix loop round %d for issue #%d", round_num + 1, state.issue_number)

    repo = config.repo_path
    git_ops = GitOperations(repo)
    await git_ops.reset_hard("HEAD")
    await git_ops.clean()

    findings_text = _format_findings(current_findings)
    context = {
        "issue_number": state.issue_number,
        "branch": state.branch or "",
        "findings_text": findings_text,
        "round": round_num + 1,
    }

    prompt = render_template(config.factory_path, "wrench_fix.j2", context)
    if not prompt:
        prompt = _build_fix_prompt(state, findings_text, round_num + 1)

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
            f"Fix loop round {round_num + 1} failed: {result.error or result.stderr[:500]}"
        )

    history = state.findings.get("history", [])
    history.extend(current_findings)
    state.findings = {"current": [], "history": history}

    state.timestamps.stage_entered = datetime.now(UTC)
    save_state(state, config.state_path)
    return state


def _format_findings(findings: list) -> str:
    lines = []
    for i, f in enumerate(findings, 1):
        sev = f.get("severity", "info")
        desc = f.get("description", f.get("raw_text", str(f)))
        cat = f.get("category", "general")
        lines.append(f"{i}. [{sev.upper()}] [{cat.upper()}] {desc}")
    return "\n".join(lines)


def _build_fix_prompt(state: PipelineState, findings_text: str, round_num: int) -> str:
    return (
        f"You are Wrench, the fix agent.\n\n"
        f"Fix the following review findings for issue #{state.issue_number}, round {round_num}.\n"
        f"Branch: {state.branch}\n\n"
        f"Findings:\n{findings_text}\n\n"
        f"All completeness and hardening findings are mandatory.\n"
        f"Commit with: fix(pipeline): address review findings round {round_num}\n\n"
        f"Before starting, read factory/AGENT-RULES.md.\n\n"
        f"RESULT_START\nstatus: success\nRESULT_END"
    )
