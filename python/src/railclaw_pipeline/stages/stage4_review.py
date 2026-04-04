"""Stage 4: Code Review — Scope performs full code review with verdict."""

import logging
import re
from datetime import UTC, datetime
from typing import Any

from railclaw_pipeline.config import PipelineConfig
from railclaw_pipeline.events.emitter import EventEmitter
from railclaw_pipeline.prompts.loader import render_template
from railclaw_pipeline.runner.agent import AgentRunner
from railclaw_pipeline.state.models import PipelineState
from railclaw_pipeline.state.persistence import save_state

logger = logging.getLogger(__name__)


async def run_review(
    state: PipelineState,
    config: PipelineConfig,
    emitter: EventEmitter,
    runner: AgentRunner,
) -> PipelineState:
    """Stage 4: Scope code review with structured verdict.

    Output: findings list WITH verdict — PASS or REVISION.
    Each review uses a fresh Scope session.
    """
    context = {
        "issue_number": state.issue_number,
        "branch": state.branch or "",
        "pr_number": state.pr_number,
    }

    prompt = render_template(config.factory_path, "scope_review.j2", context)
    if not prompt:
        prompt = _build_review_prompt(state)

    emitter.emit("agent_start", issue=state.issue_number, agent="scope", cli="opencode")
    result = await runner.run(prompt)
    emitter.emit(
        "agent_end",
        issue=state.issue_number,
        agent="scope",
        cli="opencode",
        duration_s=result.duration,
        success=result.success,
    )

    verdict = _parse_verdict(result.stdout)
    findings = _parse_review_findings(result.stdout)

    state.cycle.scope_verdict = verdict

    history = state.findings.get("history", [])
    history.extend(state.findings.get("current", []))
    state.findings = {"current": findings, "history": history}

    emitter.emit("review_result", issue=state.issue_number, verdict=verdict, findings=len(findings))

    state.timestamps.stage_entered = datetime.now(UTC)
    save_state(state, config.state_path)
    return state


def _parse_verdict(output: str) -> str:
    """Extract verdict from review output."""
    match = re.search(r"verdict:\s*(pass|revision|needs-human)", output, re.IGNORECASE)
    if match:
        return match.group(1).lower()
    if re.search(
        r"REVIEW_START.*?verdict:\s*(pass|revision|needs-human)",
        output,
        re.DOTALL | re.IGNORECASE,
    ):
        m = re.search(r"verdict:\s*(\S+)", output, re.IGNORECASE)
        if m:
            return m.group(1).lower()
    if "RESULT_START" in output and "status: success" in output:
        return "pass"
    return "revision"


def _parse_review_findings(output: str) -> list[dict[str, Any]]:
    """Parse findings from review output."""
    findings: list[dict[str, Any]] = []
    pattern = re.compile(r"FINDING_START\s*\n(.*?)\nFINDING_END", re.DOTALL)
    for match in pattern.finditer(output):
        block = match.group(1).strip()
        finding: dict[str, Any] = {}
        for line in block.splitlines():
            line = line.strip()
            if ":" in line:
                key, _, value = line.partition(":")
                finding[key.strip()] = value.strip()
        if finding:
            findings.append(finding)
    return findings


def _build_review_prompt(state: PipelineState) -> str:
    pr_info = f"PR: #{state.pr_number}\n" if state.pr_number else ""
    return (
        f"You are Scope, the code review agent.\n\n"
        f"Review the code for issue #{state.issue_number}.\n"
        f"Branch: {state.branch}\n"
        f"{pr_info}\n"
        f"Provide a verdict (pass/revision) and list any findings.\n\n"
        f"Before starting, read factory/AGENT-RULES.md.\n\n"
        f"RESULT_START\nstatus: success\nRESULT_END"
    )
