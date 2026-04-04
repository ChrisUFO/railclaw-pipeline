"""Utility: cleanup old run logs."""

import logging
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


def cleanup_old_runs(
    base_dir: Path,
    max_age_days: int = 30,
    *,
    dry_run: bool = False,
) -> list[str]:
    """Delete run logs older than max_age_days.

    Args:
        base_dir: Directory containing run subdirectories.
        max_age_days: Maximum age in days before a run is considered stale.
        dry_run: If True, log what would be deleted without actually deleting.

    Returns:
        List of deleted (or would-be-deleted) directory paths.
    """
    cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
    deleted: list[str] = []
    if not base_dir.exists():
        return deleted
    for run_dir in sorted(base_dir.iterdir()):
        if not run_dir.is_dir():
            continue
        ctime = datetime.fromtimestamp(run_dir.stat().st_ctime, tz=UTC)
        if ctime < cutoff:
            if dry_run:
                logger.info("dry-run: would delete %s (ctime=%s)", run_dir, ctime)
            else:
                shutil.rmtree(run_dir)
            deleted.append(str(run_dir))
    return deleted
