"""Tests for cleanup utility."""

import os
import time
from datetime import timedelta
from pathlib import Path

from railclaw_pipeline.utils.cleanup import cleanup_old_runs


def test_cleanup_deletes_old_dirs(tmp_path: Path):
    """Directories older than max_age_days are deleted."""
    old_dir = tmp_path / "issue-1"
    old_dir.mkdir()
    new_dir = tmp_path / "issue-2"
    new_dir.mkdir()

    # Make old_dir look old by setting mtime
    old_mtime = time.time() - (35 * 86400)
    os.utime(old_dir, (old_mtime, old_mtime))

    deleted = cleanup_old_runs(tmp_path, max_age_days=30)
    assert len(deleted) == 1
    assert str(old_dir) in deleted
    assert new_dir.exists()


def test_cleanup_skips_nonexistent(tmp_path: Path):
    """Non-existent base_dir returns empty list."""
    deleted = cleanup_old_runs(tmp_path / "nonexistent")
    assert deleted == []


def test_cleanup_skips_files(tmp_path: Path):
    """Non-directory entries are skipped."""
    (tmp_path / "somefile.txt").write_text("data")
    deleted = cleanup_old_runs(tmp_path, max_age_days=0)
    assert len(deleted) == 0
    assert (tmp_path / "somefile.txt").exists()


def test_cleanup_keeps_recent(tmp_path: Path):
    """Recent directories are not deleted."""
    recent_dir = tmp_path / "issue-recent"
    recent_dir.mkdir()

    deleted = cleanup_old_runs(tmp_path, max_age_days=30)
    assert deleted == []
    assert recent_dir.exists()


def test_cleanup_dry_run(tmp_path: Path):
    """dry_run reports what would be deleted without deleting."""
    old_dir = tmp_path / "issue-old"
    old_dir.mkdir()

    old_mtime = time.time() - (35 * 86400)
    os.utime(old_dir, (old_mtime, old_mtime))

    deleted = cleanup_old_runs(tmp_path, max_age_days=30, dry_run=True)
    assert len(deleted) == 1
    assert str(old_dir) in deleted
    assert old_dir.exists()  # still there — dry run
