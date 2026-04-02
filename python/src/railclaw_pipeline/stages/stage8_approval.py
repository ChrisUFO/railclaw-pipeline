"""Stage 8: Approval gate — wait for human approval via file protocol."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from railclaw_pipeline.config import PipelineConfig
from railclaw_pipeline.events.emitter import EventEmitter
from railclaw_pipeline.state.models import PipelineState, PipelineStatus
from railclaw_pipeline.state.persistence import save_state

logger = logging.getLogger(__name__)

APPROVAL_POLL_INTERVAL = 30
DEFAULT_APPROVAL_TIMEOUT = 86400


async def run_approval(
    state: PipelineState,
    config: PipelineConfig,
    emitter: EventEmitter,
) -> PipelineState:
    """Stage 8: Wait for human approval via file protocol.

    Protocol:
    1. Write awaiting-approval.json with PR details and summary
    2. Emit AWAITING_APPROVAL event
    3. Poll for approve-{pr}.json or abort-{pr}.json
    4. On approval: continue to merge
    5. On abort: set status to FAILED
    6. On timeout: set status to FAILED
    """
    pr_number = state.pr_number
    if not pr_number:
        raise RuntimeError("No PR number — cannot wait for approval")

    factory = config.factory_path
    awaiting_path = factory / f"awaiting-approval-{pr_number}.json"
    approve_path = factory / f"approve-{pr_number}.json"
    abort_path = factory / f"abort-{pr_number}.json"

    summary = _build_approval_summary(state)

    awaiting_data = {
        "pr_number": pr_number,
        "issue_number": state.issue_number,
        "branch": state.branch,
        "stage": state.stage.value,
        "cycle1_round": state.cycle.cycle1_round,
        "cycle2_round": state.cycle.cycle2_round,
        "summary": summary,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    awaiting_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(awaiting_path, json.dumps(awaiting_data, indent=2))

    state.status = PipelineStatus.PAUSED
    if state.timestamps:
        state.timestamps.last_updated = datetime.now(timezone.utc)
    save_state(state, config.state_path)

    emitter.emit("awaiting_approval", issue=state.issue_number, pr=pr_number, summary=summary)

    timeout = config.timing.get("approvalTimeout", DEFAULT_APPROVAL_TIMEOUT)
    decision = await _poll_for_decision(approve_path, abort_path, timeout)

    awaiting_path.unlink(missing_ok=True)

    if decision == "approved":
        state.status = PipelineStatus.RUNNING
        emitter.emit("approval_received", issue=state.issue_number, pr=pr_number, decision="approved")
    elif decision == "aborted":
        state.status = PipelineStatus.FAILED
        state.error = {"category": "approval_aborted", "message": "Pipeline aborted by user"}
        emitter.emit("approval_received", issue=state.issue_number, pr=pr_number, decision="aborted")
    else:
        state.status = PipelineStatus.FAILED
        state.error = {"category": "approval_timeout", "message": f"Approval timed out after {timeout}s"}
        emitter.emit("approval_timeout", issue=state.issue_number, pr=pr_number)

    if state.timestamps:
        state.timestamps.last_updated = datetime.now(timezone.utc)
        state.timestamps.stage_entered = datetime.now(timezone.utc)
    save_state(state, config.state_path)
    return state


async def _poll_for_decision(
    approve_path: Path,
    abort_path: Path,
    timeout: float,
) -> str:
    """Poll for approval or abort signal file.

    Returns "approved", "aborted", or "timeout".
    """
    elapsed = 0.0
    while elapsed < timeout:
        if approve_path.exists():
            try:
                approve_path.unlink()
            except OSError:
                pass
            return "approved"

        if abort_path.exists():
            try:
                abort_path.unlink()
            except OSError:
                pass
            return "aborted"

        await asyncio.sleep(APPROVAL_POLL_INTERVAL)
        elapsed += APPROVAL_POLL_INTERVAL

    return "timeout"


def _build_approval_summary(state: PipelineState) -> str:
    parts = [f"Issue #{state.issue_number}"]
    if state.branch:
        parts.append(f"Branch: {state.branch}")
    if state.pr_number:
        parts.append(f"PR: #{state.pr_number}")
    parts.append(f"Cycle 1 rounds: {state.cycle.cycle1_round}")
    parts.append(f"Cycle 2 rounds: {state.cycle.cycle2_round}")

    findings_count = len(state.findings.get("current", []))
    history_count = len(state.findings.get("history", []))
    parts.append(f"Open findings: {findings_count}")
    parts.append(f"Resolved findings: {history_count}")

    if state.cycle.gemini_clean:
        parts.append("Gemini review: CLEAN")

    return " | ".join(parts)


def _atomic_write(path: Path, data: str) -> None:
    """Atomic write using tempfile + os.replace."""
    import os
    import tempfile

    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp", prefix="approval_")
    try:
        os.write(fd, data.encode("utf-8"))
        os.fsync(fd)
        os.close(fd)
        os.replace(tmp_path, str(path))
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
