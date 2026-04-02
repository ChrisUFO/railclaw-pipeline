"""Stage 9: PM2 deploy — pull, install, restart, health check."""

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from railclaw_pipeline.config import PipelineConfig
from railclaw_pipeline.events.emitter import EventEmitter
from railclaw_pipeline.github.git import GitOperations
from railclaw_pipeline.runner.subprocess_runner import SubprocessError, run_subprocess
from railclaw_pipeline.state.models import PipelineState
from railclaw_pipeline.state.persistence import save_state

logger = logging.getLogger(__name__)

DEFAULT_HEALTH_URL = "http://localhost:3000/health"
HEALTH_CHECK_ATTEMPTS = 12
HEALTH_CHECK_INTERVAL = 5


async def run_deploy(
    state: PipelineState,
    config: PipelineConfig,
    emitter: EventEmitter,
) -> PipelineState:
    """Stage 9: Deploy via PM2 restart on the Pi.

    Steps:
    1. Ensure on main and up to date
    2. npm ci --production
    3. PM2 start or restart
    4. Health check (curl localhost:3000/health, up to 60s)
    """
    repo = config.repo_path
    git_ops = GitOperations(repo)

    emitter.emit("deploy_start", issue=state.issue_number)

    await git_ops.checkout("main")
    await git_ops.pull("origin", "main")

    emitter.emit("deploy_install_start", issue=state.issue_number)
    try:
        await run_subprocess(
            ["npm", "ci", "--production"],
            cwd=repo,
            timeout=120,
        )
    except SubprocessError as exc:
        raise RuntimeError(f"npm ci --production failed: {exc}") from exc
    emitter.emit("deploy_install_complete", issue=state.issue_number)

    pm2_config = config.pm2 or {}
    process_name = pm2_config.get("processName", "railclaw-mc")
    ecosystem_path = pm2_config.get("ecosystemPath", "ecosystem.config.cjs")

    pm2_exists = await _check_pm2_process(process_name)

    emitter.emit("deploy_pm2_start", issue=state.issue_number, exists=pm2_exists)
    try:
        if pm2_exists:
            await run_subprocess(
                ["pm2", "restart", "--update-env", process_name],
                cwd=repo,
                timeout=60,
            )
        else:
            await run_subprocess(
                ["pm2", "start", ecosystem_path, "--env", "production", "--only", process_name],
                cwd=repo,
                timeout=60,
            )
    except SubprocessError as exc:
        raise RuntimeError(
            f"pm2 {'restart' if pm2_exists else 'start'} failed: {exc}"
        ) from exc

    try:
        await run_subprocess(["pm2", "save"], timeout=15)
    except SubprocessError:
        logger.warning("pm2 save failed (non-critical)", exc_info=True)

    emitter.emit("deploy_health_check_start", issue=state.issue_number)
    health_timeout = config.timing.get("healthCheckTimeout", 60)

    healthy = await _health_check(health_timeout)
    if not healthy:
        raise RuntimeError(
            "Health check failed — service did not respond within "
            f"{HEALTH_CHECK_ATTEMPTS * HEALTH_CHECK_INTERVAL}s after PM2 restart"
        )

    emitter.emit("deploy_success", issue=state.issue_number)

    if state.timestamps:
        state.timestamps.last_updated = datetime.now(timezone.utc)
    save_state(state, config.state_path)
    return state


async def _check_pm2_process(process_name: str) -> bool:
    """Check if a PM2 process with the given name exists."""
    try:
        result = await run_subprocess(
            ["pm2", "jlist"],
            timeout=15,
        )
        import json
        processes = json.loads(result.stdout)
        return any(p.get("name") == process_name for p in processes)
    except (SubprocessError, json.JSONDecodeError, OSError):
        return False


async def _health_check(timeout_s: float) -> bool:
    """Wait for health endpoint to respond. Returns True if healthy."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        try:
            result = await run_subprocess(
                ["curl", "-sf", DEFAULT_HEALTH_URL],
                timeout=10,
            )
            if result.returncode == 0:
                return True
        except SubprocessError:
            pass
        await asyncio.sleep(HEALTH_CHECK_INTERVAL)
    return False
