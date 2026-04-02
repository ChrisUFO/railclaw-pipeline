"""Stage 10: Beaker QA sweep — run QA agent, file critical issues."""

import logging
from datetime import datetime, timezone

from railclaw_pipeline.config import PipelineConfig
from railclaw_pipeline.events.emitter import EventEmitter
from railclaw_pipeline.github.gh import GhClient
from railclaw_pipeline.prompts.loader import render_template
from railclaw_pipeline.runner.agent import AgentRunner
from railclaw_pipeline.state.models import PipelineState
from railclaw_pipeline.state.persistence import save_state

logger = logging.getLogger(__name__)


async def run_qa(
    state: PipelineState,
    config: PipelineConfig,
    emitter: EventEmitter,
    runner: AgentRunner,
) -> PipelineState:
    """Stage 10: Run Beaker QA on merged main.

    Runs Beaker QA sweep, files critical issues as GitHub issues.
    Does NOT block deployment per pipeline rules.
    """
    emitter.emit("qa_start", issue=state.issue_number, pr=state.pr_number)

    context = {
        "issue_number": state.issue_number,
        "branch": state.branch or "",
        "pr_number": state.pr_number or "",
        "repo_path": str(config.repo_path),
    }

    prompt = render_template(config.factory_path, "beaker_qa.j2", context)
    if not prompt:
        prompt = _build_qa_prompt(state)

    emitter.emit("agent_start", issue=state.issue_number, agent="beaker", cli="opencode")
    result = await runner.run(prompt)
    emitter.emit(
        "agent_end",
        issue=state.issue_number,
        agent="beaker",
        cli="opencode",
        duration_s=result.duration,
        success=result.success,
    )

    qa_findings = _parse_findings(result.stdout)

    if not result.success:
        logger.warning(
            "Beaker QA had issues for #%d (non-blocking): %s",
            state.issue_number,
            result.error or result.stderr[:300],
        )
        emitter.emit("qa_warning", issue=state.issue_number, error=result.error or "non-zero exit")

    critical_filed = []
    for finding in qa_findings:
        severity = finding.get("severity", "").upper()
        if severity in ("HIGH", "CRITICAL") and finding.get("status") != "fixed":
            filed_number = await _file_critical_issue(
                config, state, finding,
            )
            if filed_number:
                critical_filed.append(filed_number)
                emitter.emit(
                    "critical_issue_filed",
                    issue=state.issue_number,
                    filed_issue=filed_number,
                    severity=severity,
                    title=finding.get("title", finding.get("description", "")[:80]),
                )

    history = state.findings.get("history", [])
    history.extend(qa_findings)
    state.findings = {
        "current": state.findings.get("current", []),
        "history": history,
        "qa_critical_filed": critical_filed,
    }

    emitter.emit(
        "qa_complete",
        issue=state.issue_number,
        findings_count=len(qa_findings),
        critical_filed=len(critical_filed),
    )

    if state.timestamps:
        state.timestamps.last_updated = datetime.now(timezone.utc)
    save_state(state, config.state_path)
    return state


async def _file_critical_issue(
    config: PipelineConfig,
    state: PipelineState,
    finding: dict,
) -> int | None:
    """File a critical QA finding as a GitHub issue. Returns issue number or None."""
    gh = GhClient(config.repo_path)
    title = f"[Auto-filed from QA] {finding.get('title', finding.get('description', 'Untitled')[:80])}"
    body = (
        f"**Source:** Beaker QA run for PR #{state.pr_number}\n"
        f"**Severity:** {finding.get('severity', 'UNKNOWN')}\n"
        f"**Description:** {finding.get('description', 'No description')}\n"
        f"\nAuto-filed by orchestrator during Stage 10 QA."
    )
    try:
        result = await gh.issue_create(title, body, labels=["bug", "qa"])
        url = result.get("url", "")
        if url:
            import re
            match = re.search(r"/issues/(\d+)", url)
            if match:
                return int(match.group(1))
    except Exception:
        logger.warning("Failed to file critical QA issue", exc_info=True)
    return None


def _parse_findings(stdout: str) -> list[dict]:
    """Parse structured findings from Beaker output."""
    findings = []
    in_block = False
    current = {}

    for line in stdout.splitlines():
        line = line.strip()
        if line == "FINDING_START":
            in_block = True
            current = {}
            continue
        if line == "FINDING_END":
            in_block = False
            if current:
                findings.append(current)
            current = {}
            continue
        if in_block and ":" in line:
            key, _, value = line.partition(":")
            key = key.strip().lower().replace(" ", "_")
            current[key] = value.strip()

    return findings


def _build_qa_prompt(state: PipelineState) -> str:
    return (
        "You are Beaker, the QA agent.\n\n"
        f"Run a QA sweep on the merged code for issue #{state.issue_number}.\n"
        f"PR: #{state.pr_number}\n"
        f"Branch: {state.branch}\n\n"
        "Check:\n"
        "- PLAN.md completeness — verify all phases implemented\n"
        "- Build and lint pass\n"
        "- No obvious regressions\n"
        "- No missing error handling\n\n"
        "Report findings using:\n"
        "FINDING_START\n"
        "severity: HIGH|MEDIUM|LOW\n"
        "title: <title>\n"
        "description: <description>\n"
        "category: completeness|hardening|polish\n"
        "FINDING_END\n\n"
        "Before starting, read factory/AGENT-RULES.md.\n\n"
        "RESULT_START\nstatus: success\nRESULT_END"
    )
