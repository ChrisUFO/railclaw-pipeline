"""Stage 2.5: Create PR — uses gh CLI to create a pull request."""

import logging
from datetime import UTC, datetime

from railclaw_pipeline.config import PipelineConfig
from railclaw_pipeline.events.emitter import EventEmitter
from railclaw_pipeline.github.pr import PrClient
from railclaw_pipeline.state.models import PipelineState
from railclaw_pipeline.state.persistence import save_state

logger = logging.getLogger(__name__)


async def run_create_pr(
    state: PipelineState,
    config: PipelineConfig,
    emitter: EventEmitter,
) -> PipelineState:
    """Stage 2.5: Create PR via gh CLI.

    Idempotent: if PR already exists for this branch, skip creation.
    """
    if not state.branch:
        raise RuntimeError("No branch set — cannot create PR")

    pr_client = PrClient(config.repo_path)

    existing = await pr_client.find_by_head(state.branch)
    if existing:
        state.pr_number = existing.get("number")
        emitter.emit(
            "pr_exists",
            issue=state.issue_number,
            pr=state.pr_number,
            message=f"PR already exists: #{state.pr_number}",
        )
        save_state(state, config.state_path)
        return state

    title = (
        f"Issue #{state.issue_number}: {state.branch.replace('feat/issue-', '').replace('-', ' ')}"
    )
    body = f"Closes #{state.issue_number}\n\nAutomated pipeline implementation."

    result = await pr_client.create(
        title=title,
        body=body,
        base="main",
        head=state.branch,
    )

    state.pr_number = result.get("pr_number")
    if not state.pr_number:
        url = result.get("url", "")
        if "/pull/" in url:
            try:
                state.pr_number = int(url.rstrip("/").split("/")[-1])
            except (ValueError, IndexError):
                raise RuntimeError(f"Failed to parse PR number from URL: {url}") from None
        else:
            raise RuntimeError(f"PR creation returned no number: {result}")

    emitter.emit(
        "pr_created",
        issue=state.issue_number,
        pr=state.pr_number,
        branch=state.branch,
    )

    state.timestamps.stage_entered = datetime.now(UTC)
    save_state(state, config.state_path)
    return state
