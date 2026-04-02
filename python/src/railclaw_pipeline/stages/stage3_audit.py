"""Stage 3: Audit — Scope completeness audit, findings-only output."""

import logging
import re
from datetime import datetime, timezone
from typing import Any

from railclaw_pipeline.config import PipelineConfig
from railclaw_pipeline.events.emitter import EventEmitter
from railclaw_pipeline.prompts.loader import render_template
from railclaw_pipeline.runner.agent import AgentRunner
from railclaw_pipeline.state.models import PipelineState
from railclaw_pipeline.state.persistence import save_state

logger = logging.getLogger(__name__)


async def run_audit(
    state: PipelineState,
    config: PipelineConfig,
    emitter: EventEmitter,
    runner: AgentRunner,
) -> PipelineState:
    """Stage 3: Scope completeness audit.

    Produces findings list only — no verdict (PASS/REVISION).
    Checks PLAN.md deliverables against actual implementation.
    """
    context = {
        "issue_number": state.issue_number,
        "branch": state.branch or "",
        "pr_number": state.pr_number,
    }

    prompt = render_template(config.factory_path, "scope_audit.j2", context)
    if not prompt:
        prompt = _build_audit_prompt(state, config)

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

    findings = _parse_audit_findings(result.stdout)

    history = state.findings.get("history", [])
    history.extend(state.findings.get("current", []))
    state.findings = {"current": findings, "history": history}

    emitter.emit("findings", issue=state.issue_number, stage="stage3_audit", count=len(findings))

    state.timestamps.stage_entered = datetime.now(timezone.utc)
    save_state(state, config.state_path)
    return state


def _parse_audit_findings(output: str) -> list[dict[str, Any]]:
    """Parse findings from Scope audit output."""
    findings: list[dict[str, Any]] = []
    pattern = re.compile(
        r"FINDING_START\s*\n(.*?)\nFINDING_END",
        re.DOTALL,
    )
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

    if not findings:
        fallback_pattern = re.compile(
            r"\[(COMPLETENESS|HARDENING|POLISH)\]\s*(.*)",
        )
        for match in fallback_pattern.finditer(output):
            findings.append({
                "category": match.group(1).lower(),
                "description": match.group(2).strip(),
            })

    return findings


def _build_audit_prompt(state: PipelineState, config: PipelineConfig) -> str:
    return (
        f"You are Scope, the audit agent.\n\n"
        f"Perform a completeness audit for issue #{state.issue_number}.\n"
        f"Branch: {state.branch}\n\n"
        f"Check PLAN.md deliverables against actual implementation.\n"
        f"Report findings as FINDING_START/FINDING_END blocks.\n\n"
        f"Before starting, read factory/AGENT-RULES.md.\n\n"
        f"RESULT_START\nstatus: success\nRESULT_END"
    )
