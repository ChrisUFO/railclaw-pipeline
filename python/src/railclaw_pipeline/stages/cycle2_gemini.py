"""Cycle 2: Gemini review loop — poll reviews, extract findings, fix with Wrench."""

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any

from railclaw_pipeline.config import PipelineConfig
from railclaw_pipeline.events.emitter import EventEmitter
from railclaw_pipeline.github.git import GitOperations
from railclaw_pipeline.github.pr import PrClient
from railclaw_pipeline.github.review import (
    ReviewResult,
    extract_findings_from_comments,
    extract_findings_from_reviews,
    parse_details_blocks,
    poll_reviews,
)
from railclaw_pipeline.prompts.loader import render_template
from railclaw_pipeline.runner.agent import AgentConfig, AgentRunner
from railclaw_pipeline.runner.agent_config import get_agent_config
from railclaw_pipeline.state.models import PipelineState
from railclaw_pipeline.state.persistence import save_state

logger = logging.getLogger(__name__)

CYCLE2_POLL_INTERVAL = 60
CYCLE2_MAX_WAIT = 900
CYCLE2_SAFETY_CAP = 20


async def run_gemini_loop(
    state: PipelineState,
    config: PipelineConfig,
    emitter: EventEmitter,
    scope_runner: AgentRunner,
) -> PipelineState:
    """Cycle 2: Poll Gemini review, extract findings, fix with Wrench or Wrench Sr.

    Flow per round:
    1. Poll PR for Gemini reviews/comments
    2. If clean (zero findings + formal review) -> set gemini_clean, return
    3. If findings -> pass to Wrench (or Wrench Sr after round 3)
    4. Wrench fixes -> push -> Scope re-reviews
    5. Loop

    Safety cap at 20 rounds. Non-convergence triggers Chris escalation.
    """
    if not state.pr_number:
        raise RuntimeError("No PR number — cannot run Gemini review loop")

    pr_client = PrClient(config.repo_path)
    last_ts = state.gemini_tracking.get("last_gemini_timestamp")

    emitter.emit("cycle2_poll_start", issue=state.issue_number, round=state.cycle.cycle2_round)

    review_result = await _poll_with_timeout(
        pr_client, state.pr_number, last_ts, config, emitter, state,
    )

    findings = _extract_gemini_findings(review_result)

    if review_result.is_clean and review_result.has_formal_review:
        state.cycle.gemini_clean = True
        emitter.emit("cycle2_clean", issue=state.issue_number, round=state.cycle.cycle2_round)
        state.findings["current"] = []
        state.gemini_tracking["last_gemini_timestamp"] = review_result.last_processed_at
        save_state(state, config.state_path)
        return state

    if not findings and not review_result.has_formal_review:
        emitter.emit("cycle2_no_response", issue=state.issue_number, round=state.cycle.cycle2_round)
        state.gemini_tracking["pending_poll"] = True
        save_state(state, config.state_path)
        return state

    history = state.findings.get("history", [])
    history.extend(state.findings.get("current", []))
    state.findings = {"current": findings, "history": history}
    state.gemini_tracking["last_gemini_timestamp"] = review_result.last_processed_at
    save_state(state, config.state_path)

    emitter.emit("cycle2_findings", issue=state.issue_number, round=state.cycle.cycle2_round, count=len(findings))

    use_wrench_sr = state.cycle.cycle2_round >= config.escalation.get("wrenchSrAfterRound", 3) - 1
    agent_name = "wrenchSr" if use_wrench_sr else "wrench"

    if use_wrench_sr:
        await _run_wrench_sr_fix(state, config, emitter, findings)
    else:
        wrench_config = get_agent_config(config, "wrench")
        wrench_runner = AgentRunner(wrench_config, config.repo_path)
        await _run_wrench_fix(state, config, emitter, wrench_runner, findings)

    context = {
        "issue_number": state.issue_number,
        "branch": state.branch or "",
        "pr_number": state.pr_number,
    }
    prompt = render_template(config.factory_path, "scope_review.j2", context)
    if not prompt:
        prompt = _build_scope_re_review_prompt(state)

    emitter.emit("agent_start", issue=state.issue_number, agent="scope", cli="opencode")
    scope_result = await scope_runner.run(prompt)
    emitter.emit(
        "agent_end",
        issue=state.issue_number,
        agent="scope",
        cli="opencode",
        duration_s=scope_result.duration,
        success=scope_result.success,
        stdout=scope_result.stdout,
        stderr=scope_result.stderr,
    )

    scope_findings = _parse_scope_findings(scope_result.stdout)
    scope_verdict = _parse_verdict(scope_result.stdout)

    state.cycle.scope_verdict = scope_verdict

    if scope_verdict == "pass" and not scope_findings:
        state.cycle.gemini_clean = True
        state.findings["current"] = []
        emitter.emit("cycle2_scope_clean", issue=state.issue_number, round=state.cycle.cycle2_round)
    else:
        state.findings["current"] = scope_findings
        emitter.emit("cycle2_scope_findings", issue=state.issue_number, verdict=scope_verdict, count=len(scope_findings))

    state.timestamps.stage_entered = datetime.now(timezone.utc)
    save_state(state, config.state_path)
    return state


async def _poll_with_timeout(
    pr_client: PrClient,
    pr_number: int,
    last_ts: str | None,
    config: PipelineConfig,
    emitter: EventEmitter,
    state: PipelineState,
) -> ReviewResult:
    """Poll for Gemini review with timeout from config."""
    poll_interval = config.timing.get("geminiPollInterval", CYCLE2_POLL_INTERVAL)
    max_wait = CYCLE2_MAX_WAIT

    elapsed = 0
    while elapsed < max_wait:
        result = await poll_reviews(pr_client, pr_number, last_ts)

        gemini_reviews = [
            r for r in result.raw_reviews
            if r.get("author", "").lower().startswith("gemini")
        ]
        gemini_comments = [
            c for c in result.raw_comments
            if c.get("author", "").lower().startswith("gemini")
        ]

        if gemini_reviews or gemini_comments:
            has_formal = any(
                r.get("state") in ("APPROVED", "CHANGES_REQUESTED", "COMMENTED")
                for r in gemini_reviews
            )
            if has_formal and result.is_clean:
                return ReviewResult(
                    findings=[],
                    is_clean=True,
                    has_formal_review=True,
                    last_processed_at=datetime.now(timezone.utc).isoformat(),
                )
            return result

        emitter.emit("cycle2_poll_wait", issue=state.issue_number, elapsed=elapsed)
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

    emitter.emit("cycle2_poll_timeout", issue=state.issue_number)
    return ReviewResult(
        findings=[],
        is_clean=True,
        has_formal_review=False,
        last_processed_at=datetime.now(timezone.utc).isoformat(),
    )


def _extract_gemini_findings(review_result: ReviewResult) -> list[dict[str, Any]]:
    """Extract findings from review result, separating Gemini from other sources."""
    findings: list[dict[str, Any]] = []
    for f in review_result.raw_reviews:
        body = f.get("body", "")
        if not body:
            continue
        author = f.get("author", "")
        blocks = parse_details_blocks(body)
        for block in blocks:
            findings.append({
                "category": "gemini",
                "description": block.description[:500],
                "raw_text": block.raw_text,
                "source": "gemini_review",
                "author": author,
            })
        if not blocks and f.get("state") == "CHANGES_REQUESTED":
            findings.append({
                "category": "gemini",
                "description": body[:500],
                "raw_text": body,
                "source": "gemini_review",
                "author": author,
            })

    for c in review_result.raw_comments:
        body = c.get("body", "")
        if not body:
            continue
        author = c.get("author", "")
        findings.append({
            "category": "gemini-inline",
            "description": body[:500],
            "raw_text": body,
            "source": "gemini_comment",
            "author": author,
        })

    return findings


async def _run_wrench_fix(
    state: PipelineState,
    config: PipelineConfig,
    emitter: EventEmitter,
    runner: AgentRunner,
    findings: list[dict[str, Any]],
) -> None:
    """Run Wrench to fix Gemini findings."""
    repo = config.repo_path
    git_ops = GitOperations(repo)
    await git_ops.reset_hard("HEAD")
    await git_ops.clean()

    findings_text = _format_findings(findings)
    context = {
        "issue_number": state.issue_number,
        "branch": state.branch or "",
        "findings_text": findings_text,
        "round": state.cycle.cycle2_round + 1,
        "source": "gemini",
    }

    prompt = render_template(config.factory_path, "wrench_fix.j2", context)
    if not prompt:
        prompt = _build_fix_prompt(state, findings_text, "Wrench")

    emitter.emit("agent_start", issue=state.issue_number, agent="wrench", cli="opencode")
    result = await runner.run(prompt)
    emitter.emit(
        "agent_end",
        issue=state.issue_number,
        agent="wrench",
        cli="opencode",
        duration_s=result.duration,
        success=result.success,
        stdout=result.stdout,
        stderr=result.stderr,
    )

    if not result.success:
        raise RuntimeError(f"Cycle 2 Wrench fix failed: {result.error or result.stderr[:500]}")


async def _run_wrench_sr_fix(
    state: PipelineState,
    config: PipelineConfig,
    emitter: EventEmitter,
    findings: list[dict[str, Any]],
) -> None:
    """Run Wrench Sr (Gemini CLI) for complex fixes."""
    wrench_sr_config = get_agent_config(config, "wrenchSr")
    runner = AgentRunner(wrench_sr_config, config.repo_path)

    findings_text = _format_findings(findings)
    context = {
        "issue_number": state.issue_number,
        "branch": state.branch or "",
        "findings_text": findings_text,
        "round": state.cycle.cycle2_round + 1,
        "source": "gemini",
    }

    prompt = render_template(config.factory_path, "wrench_fix.j2", context)
    if not prompt:
        prompt = _build_fix_prompt(state, findings_text, "Wrench Sr")

    emitter.emit("agent_start", issue=state.issue_number, agent="wrenchSr", cli="gemini")
    result = await runner.run(prompt)
    emitter.emit(
        "agent_end",
        issue=state.issue_number,
        agent="wrenchSr",
        cli="gemini",
        duration_s=result.duration,
        success=result.success,
        stdout=result.stdout,
        stderr=result.stderr,
    )

    if not result.success:
        raise RuntimeError(f"Cycle 2 Wrench Sr fix failed: {result.error or result.stderr[:500]}")


def _format_findings(findings: list[dict[str, Any]]) -> str:
    lines = []
    for i, f in enumerate(findings, 1):
        source = f.get("source", "unknown")
        desc = f.get("description", f.get("raw_text", str(f)))
        lines.append(f"{i}. [{source}] {desc}")
    return "\n".join(lines)


def _build_fix_prompt(state: PipelineState, findings_text: str, agent: str) -> str:
    return (
        f"You are {agent}, the fix agent.\n\n"
        f"Fix the following Gemini review findings for issue #{state.issue_number}.\n"
        f"Branch: {state.branch}\n\n"
        f"Findings:\n{findings_text}\n\n"
        f"All completeness and hardening findings are mandatory.\n"
        f"Commit with: fix(cycle2): address Gemini review findings\n\n"
        f"Before starting, read factory/AGENT-RULES.md.\n\n"
        f"RESULT_START\nstatus: success\nRESULT_END"
    )


def _build_scope_re_review_prompt(state: PipelineState) -> str:
    pr_info = f"PR: #{state.pr_number}\n" if state.pr_number else ""
    return (
        f"You are Scope, the code review agent.\n\n"
        f"Re-review the code after Gemini fixes for issue #{state.issue_number}.\n"
        f"Branch: {state.branch}\n"
        f"{pr_info}\n"
        f"Provide a verdict (pass/revision) and list any remaining findings.\n\n"
        f"Before starting, read factory/AGENT-RULES.md.\n\n"
        f"RESULT_START\nstatus: success\nRESULT_END"
    )


def _parse_scope_findings(output: str) -> list[dict[str, Any]]:
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


def _parse_verdict(output: str) -> str:
    match = re.search(r"verdict:\s*(pass|revision|needs-human)", output, re.IGNORECASE)
    if match:
        return match.group(1).lower()
    if "RESULT_START" in output and "status: success" in output:
        return "pass"
    return "revision"

