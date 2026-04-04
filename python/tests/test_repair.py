"""Tests for crash recovery and state repair."""

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from railclaw_pipeline.validation.repair import (
    IssueSeverity,
    RepairEngine,
    RepairIssue,
    RepairResult,
)


@pytest.fixture
def repo_dir(tmp_path: Path) -> Path:
    d = tmp_path / "repo"
    d.mkdir()
    (d / ".git").mkdir()
    return d


@pytest.fixture
def factory_dir(tmp_path: Path) -> Path:
    d = tmp_path / "factory"
    d.mkdir()
    (d / ".pipeline-state").mkdir()
    return d


@pytest.fixture
def state_path(factory_dir: Path) -> Path:
    return factory_dir / ".pipeline-state" / "state.json"


@pytest.fixture
def lock_path(factory_dir: Path) -> Path:
    return factory_dir / ".pipeline-state" / "pipeline.lock"


@pytest.fixture
def state_dir(factory_dir: Path) -> Path:
    return factory_dir / ".pipeline-state"


@pytest.fixture
def engine(
    repo_dir: Path, factory_dir: Path, state_path: Path, lock_path: Path, state_dir: Path
) -> RepairEngine:
    return RepairEngine(
        repo_path=repo_dir,
        factory_path=factory_dir,
        state_path=state_path,
        lock_path=lock_path,
        state_dir=state_dir,
    )


class TestRepairResult:
    def test_defaults(self) -> None:
        r = RepairResult()
        assert r.issue_count == 0
        assert r.critical_count == 0

    def test_counts(self) -> None:
        r = RepairResult(
            issues=[
                RepairIssue(IssueSeverity.CRITICAL, "a", "desc", True),
                RepairIssue(IssueSeverity.WARNING, "b", "desc", True),
                RepairIssue(IssueSeverity.CRITICAL, "c", "desc", True),
            ]
        )
        assert r.issue_count == 3
        assert r.critical_count == 2

    def test_to_dict(self) -> None:
        r = RepairResult(
            issues=[RepairIssue(IssueSeverity.WARNING, "test", "desc", False)],
            fixed=["fixed1"],
            unfixable=["unfixable1"],
        )
        d = r.to_dict()
        assert d["issue_count"] == 1
        assert d["fixed_count"] == 1
        assert d["unfixable_count"] == 1


class TestRepairIssue:
    def test_dataclass_fields(self) -> None:
        i = RepairIssue(IssueSeverity.CRITICAL, "cat", "desc", True, "fix_action", "detail")
        assert i.severity == IssueSeverity.CRITICAL
        assert i.category == "cat"
        assert i.fixable is True
        assert i.fix_action == "fix_action"
        assert i.detail == "detail"


class TestRepairEngineScan:
    async def test_scan_clean_state(self, engine: RepairEngine, state_path: Path) -> None:
        state_path.write_text(
            json.dumps(
                {
                    "issue_number": 1,
                    "stage": "stage1_blueprint",
                    "status": "running",
                }
            )
        )
        result = await engine.scan()
        assert result.critical_count == 0

    async def test_scan_corrupt_state(self, engine: RepairEngine, state_path: Path) -> None:
        state_path.write_text("not json {{{")
        result = await engine.scan()
        corrupt = [i for i in result.issues if i.category == "corrupt_state"]
        assert len(corrupt) == 1
        assert corrupt[0].severity == IssueSeverity.CRITICAL
        assert corrupt[0].fixable is True

    async def test_scan_stale_lock_dead_pid(self, engine: RepairEngine, lock_path: Path) -> None:
        lock_data = json.dumps(
            {
                "pid": 999999999,
                "timestamp": "2025-01-01T00:00:00",
                "agent": "dead",
                "stage": "stage1",
                "run_id": "issue-1",
            }
        )
        lock_path.write_text(lock_data)
        result = await engine.scan()
        stale = [i for i in result.issues if i.category == "stale_lock"]
        assert len(stale) == 1
        assert stale[0].severity == IssueSeverity.CRITICAL

    async def test_scan_no_lock_no_issue(self, engine: RepairEngine) -> None:
        result = await engine.scan()
        stale = [i for i in result.issues if i.category == "stale_lock"]
        assert len(stale) == 0

    async def test_scan_missing_repo(
        self, factory_dir: Path, state_path: Path, lock_path: Path, state_dir: Path
    ) -> None:
        engine = RepairEngine(
            repo_path=Path("/nonexistent"),
            factory_path=factory_dir,
            state_path=state_path,
            lock_path=lock_path,
            state_dir=state_dir,
        )
        result = await engine.scan()
        orphaned = [i for i in result.issues if i.category == "orphaned_branch"]
        assert len(orphaned) == 0

    async def test_scan_uncommitted_changes(self, engine: RepairEngine, state_path: Path) -> None:
        state_path.write_text(
            json.dumps({"issue_number": 1, "stage": "stage1_blueprint", "status": "running"})
        )
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(b" M file.py\n", b""))
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await engine.scan()
        uncommitted = [i for i in result.issues if i.category == "uncommitted_changes"]
        assert len(uncommitted) == 1


class TestRepairEngineFix:
    async def test_fix_corrupt_state(
        self, engine: RepairEngine, state_path: Path, state_dir: Path
    ) -> None:
        state_path.write_text("not json {{{")
        result = await engine.repair(force=True)
        corrupt_dir = state_dir / "corrupt"
        assert corrupt_dir.exists()
        corrupt_files = list(corrupt_dir.glob("*.corrupt"))
        assert len(corrupt_files) == 1

    async def test_fix_stale_lock(self, engine: RepairEngine, lock_path: Path) -> None:
        lock_data = json.dumps(
            {
                "pid": 999999999,
                "timestamp": "2025-01-01T00:00:00",
                "agent": "dead",
                "stage": "stage1",
                "run_id": "issue-1",
            }
        )
        lock_path.write_text(lock_data)
        result = await engine.repair(force=True)
        assert not lock_path.exists()
        assert len(result.fixed) >= 1

    async def test_repair_without_force_skips_critical(
        self, engine: RepairEngine, lock_path: Path
    ) -> None:
        lock_data = json.dumps(
            {
                "pid": 999999999,
                "timestamp": "2025-01-01T00:00:00",
                "agent": "dead",
                "stage": "stage1",
                "run_id": "issue-1",
            }
        )
        lock_path.write_text(lock_data)
        result = await engine.repair(force=False)
        assert len(result.unfixable) >= 1

    async def test_repair_reports_unfixable(self, engine: RepairEngine, state_path: Path) -> None:
        state_path.write_text(
            json.dumps(
                {
                    "issue_number": 1,
                    "stage": "stage3_audit",
                    "status": "running",
                    "pr_number": 999999,
                }
            )
        )
        proc = AsyncMock()
        proc.returncode = 1
        proc.communicate = AsyncMock(return_value=(b"", b"not found"))
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await engine.scan()
        missing_pr = [i for i in result.issues if i.category == "missing_pr"]
        assert len(missing_pr) == 1
        assert missing_pr[0].fixable is False
