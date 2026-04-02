"""Stage 3.5: Audit fix — Wrench fixes all audit findings verbatim."""

import logging
from datetime import datetime, timezone

from railclaw_pipeline.config import PipelineConfig
from railclaw_pipeline.events.emitter import EventEmitter
from railclaw_pipeline.github.git import GitOperations
from railclaw_pipeline.prompts.loader import render_template
from railclaw_pipeline.runner.agent import AgentRunner
from railclaw_pipeline.state.models import PipelineState
from railclaw_pipeline.state.persistence import save_state

logger = logging.getLogger(__name__)


async def run_audit_fix(
    state: PipelineState,
    config: PipelineConfig,
    emitter: EventEmitter,
    runner: AgentRunner,
) -> PipelineState:
    """Stage 3.5: Wrench fixes all audit findings.

    All completeness and hardening findings are mandatory.
    Polish findings are discretionary but included in prompt.
    On resume: git reset + clean to discard partial work.
    """
    findings = state.findings.get("current", [])
    if not findings:
        emitter.emit("audit_clean", issue=state.issue_number, stage="stage3.5_audit_fix")
        return state

    repo = config.repo_path
    git_ops = GitOperations(repo)
    await git_ops.reset_hard("HEAD")
    await git_ops.clean()

    findings_text = _format_findings(findings)
    context = {
        "issue_number": state.issue_number,
        "branch": state.branch or "",
        "findings_text": findings_text,
        "round": state.cycle.cycle1_round,
    }

    prompt = render_template(config.factory_path, "wrench_fix.j2", context)
    if not prompt:
        prompt = _build_fix_prompt(state, findings_text)

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
        raise RuntimeError(f"Audit fix failed: {result.error or result.stderr[:500]}")

    state.findings["current"] = []
    state.timestamps.stage_entered = datetime.now(timezone.utc)
    save_state(state, config.state_path)
    return state


def _format_findings(findings: list) -> str:
    lines = []
    for i, f in enumerate(findings, 1):
        category = f.get("category", "unknown")
        desc = f.get("description", f.get("raw_text", str(f)))
        lines.append(f"{i}. [{category.upper()}] {desc}")
    return "\n".join(lines)


def _build_fix_prompt(state: PipelineState, findings_text: str) -> str:
    return (
        f"You are Wrench, the fix agent.\n\n"
        f"Fix the following audit findings for issue #{state.issue_number}.\n"
        f"Branch: {state.branch}\n\n"
        f"Findings:\n{findings_text}\n\n"
        f"All completeness and hardening findings are mandatory.\n"
        f"Polish findings are discretionary.\n\n"
        f"Before starting, read factory/AGENT-RULES.md.\n\n"
        f"RESULT_START\nstatus: success\nRESULT_END"
    )
