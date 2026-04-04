"""CHECKPOINT.md helpers — create, read, sign-off, archive."""

import contextlib
import re
from datetime import UTC, datetime
from pathlib import Path


class CheckpointError(Exception):
    """Raised when checkpoint operations fail."""
    pass


CHECKPOINT_PATH = "CHECKPOINT.md"
ARCHIVE_DIR = ".pipeline-state/archive"


def get_checkpoint_path(factory_path: Path) -> Path:
    """Get path to active CHECKPOINT.md."""
    return factory_path / CHECKPOINT_PATH


def read_checkpoint(factory_path: Path) -> str:
    """Read current checkpoint content. Returns empty string if missing."""
    path = get_checkpoint_path(factory_path)
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CheckpointError(f"Failed to read checkpoint: {exc}") from exc


def write_checkpoint(factory_path: Path, content: str) -> None:
    """Write checkpoint atomically."""
    path = get_checkpoint_path(factory_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    import os
    import tempfile
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp", prefix="checkpoint_")
    try:
        os.write(fd, content.encode("utf-8"))
        os.fsync(fd)
        os.close(fd)
        os.replace(tmp_path, str(path))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


def update_checkpoint(
    factory_path: Path,
    stage: str,
    status: str,
    issue_number: int | None = None,
    notes: str | None = None,
) -> None:
    """Update CHECKPOINT.md with current stage and status.

    Format:
    # Pipeline Checkpoint
    - **Issue:** #N
    - **Stage:** stage_name
    - **Status:** status
    - **Updated:** ISO timestamp
    - **Notes:** ...
    """
    now = datetime.now(UTC).isoformat()
    issue_line = f"- **Issue:** #{issue_number}" if issue_number else "- **Issue:** N/A"
    notes_line = f"- **Notes:** {notes}" if notes else ""

    content = f"""# Pipeline Checkpoint

{issue_line}
- **Stage:** {stage}
- **Status:** {status}
- **Updated:** {now}
{notes_line}
"""
    write_checkpoint(factory_path, content)


def sign_off_checkpoint(factory_path: Path, sign_off: str) -> None:
    """Append a sign-off line to the current checkpoint."""
    existing = read_checkpoint(factory_path)
    timestamp = datetime.now(UTC).isoformat()
    sign_line = f"\n---\n✅ Signed off by {sign_off} at {timestamp}\n"
    write_checkpoint(factory_path, existing + sign_line)


def archive_checkpoint(factory_path: Path, issue_number: int) -> Path:
    """Archive the current checkpoint for an issue.

    Returns path to archived file.
    """
    content = read_checkpoint(factory_path)
    if not content:
        raise CheckpointError("No checkpoint to archive")

    archive_path = factory_path / ARCHIVE_DIR
    archive_path.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    dest = archive_path / f"checkpoint-issue-{issue_number}-{timestamp}.md"
    dest.write_text(content, encoding="utf-8")

    # Clear active checkpoint
    write_checkpoint(factory_path, "")
    return dest


def parse_checkpoint_stage(factory_path: Path) -> tuple[str, str] | None:
    """Parse stage and status from checkpoint.

    Returns (stage, status) or None if no checkpoint.
    """
    content = read_checkpoint(factory_path)
    if not content.strip():
        return None

    stage_match = re.search(r"\*\*Stage:\*\*\s*(.+)", content)
    status_match = re.search(r"\*\*Status:\*\*\s*(.+)", content)

    if not stage_match or not status_match:
        return None

    return stage_match.group(1).strip(), status_match.group(1).strip()
