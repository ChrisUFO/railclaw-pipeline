"""Utility: cleanup old run logs."""

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path


def cleanup_old_runs(base_dir: Path, max_age_days: int = 30) -> list[str]:
    """Delete run logs older than max_age_days. Returns list of deleted dirs."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    deleted: list[str] = []
    if not base_dir.exists():
        return deleted
    for run_dir in base_dir.iterdir():
        if not run_dir.is_dir():
            continue
        mtime = datetime.fromtimestamp(run_dir.stat().st_mtime, tz=timezone.utc)
        if mtime < cutoff:
            shutil.rmtree(run_dir)
            deleted.append(str(run_dir))
    return deleted
