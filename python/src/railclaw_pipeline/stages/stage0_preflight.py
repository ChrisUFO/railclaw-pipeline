"""Stage 0: Preflight — verify environment readiness before pipeline."""

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from railclaw_pipeline.config import PipelineConfig
from railclaw_pipeline.events.emitter import EventEmitter
from railclaw_pipeline.github.git import GitOperations
from railclaw_pipeline.state.models import PipelineState
from railclaw_pipeline.state.persistence import save_state

logger = logging.getLogger(__name__)


class PreflightError(Exception):
    """Raised when preflight checks fail."""
    pass


async def run_preflight(
    state: PipelineState,
    config: PipelineConfig,
    emitter: EventEmitter,
) -> PipelineState:
    """Stage 0: Verify environment before starting pipeline.

    Checks:
    1. On main branch with clean working tree
    2. Repo is up to date with remote
    3. factory/ directory exists
    4. Required tools (gh, git) are available
    """
    repo = config.repo_path

    if not repo.exists():
        raise PreflightError(f"Repo path does not exist: {repo}")

    git_ops = GitOperations(repo)

    branch = await git_ops.current_branch()
    if branch != "main":
        raise PreflightError(f"Not on main branch (current: {branch})")

    if await git_ops.is_dirty():
        raise PreflightError("Working tree not clean — commit or stash changes first")

    await git_ops.fetch("origin")
    await git_ops.pull("origin", "main")

    if not config.factory_path.exists():
        raise PreflightError(f"factory/ directory not found at {config.factory_path}")

    for tool_name in ("gh", "git"):
        proc = await asyncio.create_subprocess_exec(
            "which", tool_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        if proc.returncode != 0:
            raise PreflightError(f"Required tool not found: {tool_name}")

    emitter.emit("preflight_pass", issue=state.issue_number)
    state.timestamps.stage_entered = datetime.now(timezone.utc)
    save_state(state, config.state_path)
    return state
