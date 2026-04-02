"""Stage 11: Hotfix — post-hoc review for direct-to-main emergency fixes.

Hotfixes bypass the standard pipeline (skip stages 1-2.5) but ALWAYS get post-hoc review.
Findings → new branch → Wrench fixes → PR → Scope re-review → approval → merge.
"""

import logging
from datetime import datetime, timezone

from railclaw_pipeline.config import PipelineConfig
from railclaw_pipeline.events.emitter import EventEmitter
from railclaw_pipeline.github.git import GitOperations
from railclaw_pipeline.github.gh import GhClient
from railclaw_pipeline.github.pr import PrClient
from railclaw_pipeline.prompts.loader import render_template
from railclaw_pipeline.runner.agent import AgentRunner
from railclaw_pipeline.state.models import PipelineState
from railclaw_pipeline.state.persistence import save_state

logger = logging.getLogger(__name__)


async def run_hotfix(
    state: PipelineState,
    config: PipelineConfig,
    emitter: EventEmitter,
    wrench_runner: AgentRunner,
    scope_runner: AgentRunner,
) -> PipelineState:
    """Stage 11: Post-hoc review for a direct-to-main hotfix.

    Flow:
    1. Get hotfix diff (git diff main~1 main)
    2. Scope reviews the diff
    3. If findings → new branch → Wrench fixes → PR → Scope re-review
    4. File regression issues if needed
    5. If no findings → proceed
    """
    git_ops = GitOperations(config.repo_path)
    pr_client = PrClient(config.repo_path)
    gh_client = GhClient(config.repo_path)

    emitter.emit("hotfix_start", issue=state.issue_number)

    diff = await git_ops._git("diff", "main~1", "main")

    context = {
        "issue_number": state.issue_number,
        "diff": diff[:8000],
        "repo_path": str(config.repo_path),
    }

    prompt = render_template(config.factory_path, "scope_audit.j2", context)
    if not prompt:
        prompt = _build_hotfix_review_prompt(state, diff)

    emitter.emit("agent_start", issue=state.issue_number, agent="scope", cli="opencode")
    result = await scope_runner.run(prompt)
    emitter.emit(
        "agent_end",
        issue=state.issue_number,
        agent="scope",
        cli="opencode",
        duration_s=result.duration,
        success=result.success,
    )

    findings = _parse_hotfix_findings(result.stdout)

    if not findings:
        emitter.emit("hotfix_clean", issue=state.issue_number)
        if state.timestamps:
            state.timestamps.last_updated = datetime.now(timezone.utc)
        save_state(state, config.state_path)
        return state

    emitter.emit("hotfix_findings", issue=state.issue_number, count=len(findings))

    fix_branch = f"hotfix/{state.issue_number}-post-hoc-fixes"
    await git_ops.checkout_new(fix_branch, "main")

    findings_text = "\n".join(
        f"- [{f.get('severity', 'info').upper()}] {f.get('description', str(f))}"
        for f in findings
    )

    fix_context = {
        "issue_number": state.issue_number,
        "findings_text": findings_text,
        "is_hotfix": True,
    }
    fix_prompt = render_template(config.factory_path, "wrench_fix.j2", fix_context)
    if not fix_prompt:
        fix_prompt = _build_hotfix_fix_prompt(state, findings_text)

    emitter.emit("agent_start", issue=state.issue_number, agent="wrench", cli="opencode")
    fix_result = await wrench_runner.run(fix_prompt)
    emitter.emit(
        "agent_end",
        issue=state.issue_number,
        agent="wrench",
        cli="opencode",
        duration_s=fix_result.duration,
        success=fix_result.success,
    )

    if not fix_result.success:
        raise RuntimeError(
            f"Hotfix Wrench fix failed: {fix_result.error or fix_result.stderr[:500]}"
        )

    await git_ops.push("origin", fix_branch, set_upstream=True)

    pr_body = (
        f"Post-hoc review fixes for hotfix on issue #{state.issue_number}\n\n"
        f"Findings addressed:\n{findings_text}"
    )
    pr_result = await pr_client.create(
        title=f"Hotfix: #{state.issue_number} post-hoc fixes",
        body=pr_body,
        base="main",
        head=fix_branch,
    )
    state.pr_number = pr_result.get("pr_number")

    review_context = {
        "issue_number": state.issue_number,
        "pr_number": state.pr_number or "",
        "repo_path": str(config.repo_path),
    }
    review_prompt = render_template(config.factory_path, "scope_review.j2", review_context)
    if not review_prompt:
        review_prompt = f"Review PR #{state.pr_number} for hotfix on issue #{state.issue_number}."

    emitter.emit("agent_start", issue=state.issue_number, agent="scope", cli="opencode")
    review_result = await scope_runner.run(review_prompt)
    emitter.emit(
        "agent_end",
        issue=state.issue_number,
        agent="scope",
        cli="opencode",
        duration_s=review_result.duration,
        success=review_result.success,
    )

    regression_findings = _parse_hotfix_findings(review_result.stdout)
    for finding in regression_findings:
        severity = finding.get("severity", "").upper()
        if severity in ("HIGH", "CRITICAL"):
            try:
                await gh_client.issue_create(
                    title=f"Regression from hotfix on issue #{state.issue_number}",
                    body=f"- {finding.get('description', str(finding))}",
                    labels=["bug", "regression"],
                )
                emitter.emit("regression_filed", issue=state.issue_number)
            except Exception:
                logger.warning("Failed to file regression issue", exc_info=True)

    history = state.findings.get("history", [])
    history.extend(findings)
    state.findings = {"current": regression_findings, "history": history}

    if state.timestamps:
        state.timestamps.last_updated = datetime.now(timezone.utc)
    save_state(state, config.state_path)
    return state


def _parse_hotfix_findings(stdout: str) -> list[dict]:
    """Parse findings from hotfix review output."""
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


def _build_hotfix_review_prompt(state: PipelineState, diff: str) -> str:
    return (
        "You are Scope, the review agent.\n\n"
        f"Review this hotfix diff for issue #{state.issue_number}.\n\n"
        f"Diff:\n{diff[:6000]}\n\n"
        "Report findings using:\n"
        "FINDING_START\n"
        "severity: HIGH|MEDIUM|LOW\n"
        "description: <description>\n"
        "FINDING_END\n\n"
        "RESULT_START\nstatus: success\nRESULT_END"
    )


def _build_hotfix_fix_prompt(state: PipelineState, findings_text: str) -> str:
    return (
        "You are Wrench, the fix agent.\n\n"
        f"Fix these hotfix review findings for issue #{state.issue_number}.\n\n"
        f"Findings:\n{findings_text}\n\n"
        "Commit with: fix(hotfix): address post-hoc review findings\n\n"
        "Before starting, read factory/AGENT-RULES.md.\n\n"
        "RESULT_START\nstatus: success\nRESULT_END"
    )
