"""Stage 12: Lessons learned — generate entry, archive checkpoint.

Runs in the finally block of every pipeline run (success or failure).
"""

import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from railclaw_pipeline.config import PipelineConfig
from railclaw_pipeline.events.emitter import EventEmitter
from railclaw_pipeline.state.models import PipelineState, PipelineStatus
from railclaw_pipeline.state.persistence import save_state

logger = logging.getLogger(__name__)

LESSONS_TEMPLATE = """## Issue #{issue_number} — {timestamp}

**Result:** {result}
**Duration:** {duration}
**Branch:** {branch}
**PR:** #{pr_number}

### What Worked
- (Fill in during review)

### What Didn't
- (Fill in during review)

### Actionable Improvements
- (Fill in during review)

### Process Changes (if any)
- (Fill in during review)

### Deferred Items Audit
Verify every deferred item has a corresponding GitHub issue:
- (List each deferred item with its GitHub issue number)

### Process Violations
Any violations caught during this run (even if functionally correct):
- (List violations by stage, or "No process violations detected.")

---

"""


async def run_lessons(
    state: PipelineState,
    config: PipelineConfig,
    emitter: EventEmitter,
) -> PipelineState:
    """Stage 12: Write lessons learned entry and archive checkpoint.

    This is called from the pipeline's finally block, so it must not raise.
    """
    lessons_path = config.factory_path / "lessons-learned.md"

    duration = _compute_duration(state)
    result = state.status.value if state.status else "unknown"

    entry = LESSONS_TEMPLATE.format(
        issue_number=state.issue_number,
        timestamp=datetime.now(timezone.utc).isoformat(),
        result=result,
        duration=duration,
        branch=state.branch or "N/A",
        pr_number=state.pr_number or "N/A",
    )

    entry += _append_findings_summary(state)
    entry += _append_error_summary(state)

    lessons_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_append(lessons_path, entry)

    emitter.emit(
        "lessons_written",
        issue=state.issue_number,
        payload={"path": str(lessons_path), "result": result},
    )

    if state.timestamps:
        state.timestamps.last_updated = datetime.now(timezone.utc)
    save_state(state, config.state_path)
    return state


def _compute_duration(state: PipelineState) -> str:
    """Compute human-readable duration from timestamps."""
    if not state.timestamps:
        return "N/A"

    start = state.timestamps.started
    end = state.timestamps.last_updated
    delta = end - start
    total_seconds = int(delta.total_seconds())

    if total_seconds < 60:
        return f"{total_seconds}s"
    elif total_seconds < 3600:
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        return f"{minutes}m {seconds}s"
    else:
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        return f"{hours}h {minutes}m"


def _append_findings_summary(state: PipelineState) -> str:
    """Append findings summary to the lessons entry."""
    current = state.findings.get("current", [])
    history = state.findings.get("history", [])

    if not current and not history:
        return ""

    lines = ["### Findings Summary\n"]
    if history:
        lines.append(f"- Resolved findings: {len(history)}")
    if current:
        lines.append(f"- Open findings: {len(current)}")
        for i, f in enumerate(current[:10], 1):
            desc = f.get("description", f.get("raw_text", str(f)))[:100]
            sev = f.get("severity", "info")
            lines.append(f"  {i}. [{sev.upper()}] {desc}")

    lines.append("")
    return "\n".join(lines)


def _append_error_summary(state: PipelineState) -> str:
    """Append error summary to the lessons entry."""
    if not state.error:
        return ""

    lines = [
        "### Error Details\n",
        f"- **Category:** {state.error.get('category', 'unknown')}",
        f"- **Message:** {state.error.get('message', 'N/A')[:200]}",
        f"- **Stage:** {state.error.get('stage', state.stage.value)}",
        "",
    ]
    return "\n".join(lines)


def _atomic_append(path: Path, data: str) -> None:
    """Safely append data to a file using atomic write pattern."""
    try:
        if path.exists():
            existing = path.read_text(encoding="utf-8")
        else:
            existing = ""

        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent), suffix=".tmp", prefix="lessons_"
        )
        try:
            os.write(fd, (existing + data).encode("utf-8"))
            os.fsync(fd)
            os.close(fd)
            os.replace(tmp_path, str(path))
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception:
        logger.warning("Failed to write lessons learned", exc_info=True)
        try:
            with open(path, "a") as f:
                f.write(data)
        except Exception:
            logger.error("Fallback lessons write also failed", exc_info=True)
