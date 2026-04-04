"""Stage 8c: Pre-merge validation and squash merge."""

import asyncio
import logging
from datetime import UTC, datetime

from railclaw_pipeline.config import PipelineConfig
from railclaw_pipeline.events.emitter import EventEmitter
from railclaw_pipeline.github.git import GitOperations
from railclaw_pipeline.github.pr import PrClient
from railclaw_pipeline.state.models import PipelineState
from railclaw_pipeline.state.persistence import save_state

logger = logging.getLogger(__name__)

MERGE_RETRY_COUNT = 3
MERGE_RETRY_DELAY = 10


async def run_merge(
    state: PipelineState,
    config: PipelineConfig,
    emitter: EventEmitter,
) -> PipelineState:
    """Stage 8c: Pre-merge validation + squash merge + branch cleanup.

    Steps:
    1. Verify PR is mergeable
    2. Verify CI status (if available)
    3. Generate merge summary
    4. Squash merge via gh
    5. Delete feature branch (local + remote)
    6. Pull latest main
    """
    pr_number = state.pr_number
    if not pr_number:
        raise RuntimeError("No PR number — cannot merge")

    pr_client = PrClient(config.repo_path)
    git_ops = GitOperations(config.repo_path)

    mergeable, merge_state = await pr_client.is_mergeable(pr_number)

    if not mergeable:
        for attempt in range(MERGE_RETRY_COUNT):
            emitter.emit(
                "merge_retry",
                issue=state.issue_number,
                pr=pr_number,
                attempt=attempt + 1,
                state=merge_state,
            )
            await asyncio.sleep(MERGE_RETRY_DELAY * (attempt + 1))
            mergeable, merge_state = await pr_client.is_mergeable(pr_number)
            if mergeable:
                break

        if not mergeable:
            raise RuntimeError(
                f"PR #{pr_number} is not mergeable (state: {merge_state}). "
                f"Resolve conflicts or CI failures before merging."
            )

    emitter.emit("merge_start", issue=state.issue_number, pr=pr_number)

    try:
        merge_output = await pr_client.merge(pr_number, merge_method="squash")
        emitter.emit(
            "merge_complete",
            issue=state.issue_number,
            pr=pr_number,
            output=merge_output[:500],
        )
    except Exception as exc:
        raise RuntimeError(f"Failed to merge PR #{pr_number}: {exc}") from exc

    await git_ops.checkout("main")
    await git_ops.pull("origin", "main")

    if state.branch:
        try:
            await git_ops.delete_branch(state.branch, force=True)
        except Exception:
            logger.warning("Failed to delete local branch %s", state.branch, exc_info=True)

        try:
            await git_ops.delete_remote_branch(state.branch)
        except Exception:
            logger.warning("Failed to delete remote branch %s", state.branch, exc_info=True)

    state.timestamps.stage_entered = datetime.now(UTC)
    if state.timestamps:
        state.timestamps.last_updated = datetime.now(UTC)
    save_state(state, config.state_path)
    return state
