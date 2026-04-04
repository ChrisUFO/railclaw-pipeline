"""Crash recovery and state repair — detects and fixes broken pipeline state."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from railclaw_pipeline.state.lock import StateLock
from railclaw_pipeline.state.pid import is_pid_alive


class IssueSeverity(str, Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


@dataclass
class RepairIssue:
    """A single detected issue with optional fix."""

    severity: IssueSeverity
    category: str
    description: str
    fixable: bool
    fix_action: str = ""
    detail: str = ""


@dataclass
class RepairResult:
    """Result of a repair scan/fix run."""

    issues: list[RepairIssue] = field(default_factory=list)
    fixed: list[str] = field(default_factory=list)
    unfixable: list[str] = field(default_factory=list)

    @property
    def issue_count(self) -> int:
        return len(self.issues)

    @property
    def critical_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == IssueSeverity.CRITICAL)

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_count": self.issue_count,
            "critical_count": self.critical_count,
            "fixed_count": len(self.fixed),
            "unfixable_count": len(self.unfixable),
            "issues": [
                {
                    "severity": i.severity.value,
                    "category": i.category,
                    "description": i.description,
                    "fixable": i.fixable,
                    "detail": i.detail,
                }
                for i in self.issues
            ],
            "fixed": self.fixed,
            "unfixable": self.unfixable,
        }


class RepairEngine:
    """Detects and repairs broken pipeline state after crashes, reboots, or process kills."""

    def __init__(
        self,
        repo_path: Path,
        factory_path: Path,
        state_path: Path,
        lock_path: Path,
        state_dir: Path,
        lock_max_age: float = 14400,
    ) -> None:
        self.repo_path = repo_path
        self.factory_path = factory_path
        self.state_path = state_path
        self.lock_path = lock_path
        self.state_dir = state_dir
        self.lock_max_age = lock_max_age

    async def scan(self) -> RepairResult:
        """Run all detectors and return issues without fixing."""
        result = RepairResult()
        detectors = [
            self._detect_stale_lock,
            self._detect_orphaned_branches,
            self._detect_uncommitted_changes,
            self._detect_corrupt_state,
            self._detect_missing_pr,
            self._detect_dangling_processes,
        ]
        for detector in detectors:
            try:
                issues = await detector()
                result.issues.extend(issues)
            except Exception as exc:
                result.issues.append(
                    RepairIssue(
                        severity=IssueSeverity.WARNING,
                        category="detector_error",
                        description=f"Detector {detector.__name__} failed: {exc}",
                        fixable=False,
                    )
                )
        return result

    async def repair(self, force: bool = False) -> RepairResult:
        """Scan and auto-fix all safe issues. With force=True, fix dangerous ones too."""
        result = await self.scan()

        for issue in list(result.issues):
            if not issue.fixable:
                result.unfixable.append(f"[{issue.category}] {issue.description}")
                continue

            if issue.severity == IssueSeverity.CRITICAL and not force:
                result.unfixable.append(
                    f"[{issue.category}] {issue.description} (use --force to fix)"
                )
                continue

            try:
                fix_method = getattr(self, f"_fix_{issue.fix_action}", None)
                if fix_method:
                    await fix_method()
                    result.fixed.append(f"[{issue.category}] {issue.description}")
                    result.issues.remove(issue)
            except Exception as exc:
                result.unfixable.append(f"[{issue.category}] Fix failed: {exc}")

        return result

    async def _detect_stale_lock(self) -> list[RepairIssue]:
        """1. Stale lock detection — check if lock PID is alive; if dead and age > 5 min, flag."""
        issues: list[RepairIssue] = []
        if not self.lock_path.exists():
            return issues

        lock = StateLock(self.lock_path, max_age=self.lock_max_age)
        info = lock.get_info()
        if info is None:
            issues.append(
                RepairIssue(
                    severity=IssueSeverity.CRITICAL,
                    category="stale_lock",
                    description="Lock file exists but is unreadable.",
                    fixable=True,
                    fix_action="stale_lock",
                )
            )
            return issues

        pid_alive = is_pid_alive(info.pid)
        if not pid_alive:
            issues.append(
                RepairIssue(
                    severity=IssueSeverity.CRITICAL,
                    category="stale_lock",
                    description=f"Lock held by dead PID {info.pid} (stage: {info.stage}).",
                    fixable=True,
                    fix_action="stale_lock",
                )
            )
            return issues

        try:
            lock_age = time.time() - os.path.getmtime(self.lock_path)
        except OSError:
            lock_age = 0

        if lock_age > 300:
            issues.append(
                RepairIssue(
                    severity=IssueSeverity.WARNING,
                    category="stale_lock",
                    description=f"Lock is {lock_age:.0f}s old (PID {info.pid} still alive).",
                    fixable=True,
                    fix_action="stale_lock",
                )
            )

        return issues

    async def _detect_orphaned_branches(self) -> list[RepairIssue]:
        """2. Orphaned branches — find feat/issue-* or fix/issue-* with no open PR."""
        issues: list[RepairIssue] = []
        if not self.repo_path.exists():
            return issues

        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                str(self.repo_path),
                "branch",
                "--list",
                "feat/issue-*",
                "fix/issue-*",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return issues

            branches = [
                b.strip().lstrip("* ") for b in stdout.decode().strip().split("\n") if b.strip()
            ]
            if not branches:
                return issues

            try:
                pr_proc = await asyncio.create_subprocess_exec(
                    "gh",
                    "pr",
                    "list",
                    "--state",
                    "open",
                    "--json",
                    "headRefName",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                pr_stdout, _ = await pr_proc.communicate()
                if pr_proc.returncode == 0 and pr_stdout.strip():
                    open_pr_heads = {
                        pr.get("headRefName", "") for pr in json.loads(pr_stdout.decode())
                    }
                else:
                    open_pr_heads = set()
            except (FileNotFoundError, json.JSONDecodeError):
                open_pr_heads = set()

            for branch in branches:
                if branch not in open_pr_heads:
                    issues.append(
                        RepairIssue(
                            severity=IssueSeverity.WARNING,
                            category="orphaned_branch",
                            description=f"Branch '{branch}' has no open PR.",
                            fixable=True,
                            fix_action="orphaned_branch",
                            detail=branch,
                        )
                    )
        except (OSError, FileNotFoundError):
            pass

        return issues

    async def _detect_uncommitted_changes(self) -> list[RepairIssue]:
        """3. Uncommitted changes — detect working tree modifications."""
        issues: list[RepairIssue] = []
        if not self.repo_path.exists():
            return issues

        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                str(self.repo_path),
                "status",
                "--porcelain",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if stdout.strip():
                changes = stdout.decode().strip()
                issues.append(
                    RepairIssue(
                        severity=IssueSeverity.WARNING,
                        category="uncommitted_changes",
                        description=f"Working tree has uncommitted changes:\n{changes[:200]}",
                        fixable=True,
                        fix_action="uncommitted_changes",
                    )
                )
        except (OSError, FileNotFoundError):
            pass

        return issues

    async def _detect_corrupt_state(self) -> list[RepairIssue]:
        """4. Corrupt state files — validate JSON structure."""
        issues: list[RepairIssue] = []
        if not self.state_path.exists():
            return issues

        try:
            content = self.state_path.read_text()
            json.loads(content)
        except (json.JSONDecodeError, OSError) as exc:
            issues.append(
                RepairIssue(
                    severity=IssueSeverity.CRITICAL,
                    category="corrupt_state",
                    description=f"State file is corrupt: {exc}",
                    fixable=True,
                    fix_action="corrupt_state",
                )
            )

        return issues

    async def _detect_missing_pr(self) -> list[RepairIssue]:
        """5. Missing PR — state says Stage 2.5 complete but no PR exists."""
        issues: list[RepairIssue] = []
        if not self.state_path.exists():
            return issues

        try:
            state_data = json.loads(self.state_path.read_text())
            pr_number = state_data.get("pr_number")
            stage = state_data.get("stage", "")
        except (json.JSONDecodeError, OSError):
            return issues

        if not pr_number:
            return issues

        stage_value = str(stage)
        post_pr_stages = [
            "stage3_audit",
            "stage3.5_audit_fix",
            "stage4_review",
            "stage5_fix_loop",
            "cycle2_gemini_loop",
            "stage7_docs",
            "stage8_approval",
            "stage8c_merge",
            "stage9_deploy",
            "stage10_qa",
            "stage11_hotfix",
            "stage12_lessons",
        ]
        if stage_value not in post_pr_stages:
            return issues

        try:
            proc = await asyncio.create_subprocess_exec(
                "gh",
                "pr",
                "view",
                str(pr_number),
                "--json",
                "state",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                issues.append(
                    RepairIssue(
                        severity=IssueSeverity.CRITICAL,
                        category="missing_pr",
                        description=f"State references PR #{pr_number} but it does not exist on GitHub.",
                        fixable=False,
                        detail=f"Pipeline is at stage {stage_value}",
                    )
                )
        except FileNotFoundError:
            pass

        return issues

    async def _detect_dangling_processes(self) -> list[RepairIssue]:
        """6. Dangling agent processes — check for subprocesses with pipeline metadata."""
        issues: list[RepairIssue] = []
        pid_path = self.state_dir / "pipeline.pid"
        if not pid_path.exists():
            return issues

        try:
            content = pid_path.read_text().strip()
            pid = int(content.split("\n")[0])
        except (ValueError, OSError):
            return issues

        if not is_pid_alive(pid):
            return issues

        if sys.platform == "win32":
            try:
                proc = await asyncio.create_subprocess_exec(
                    "tasklist",
                    "/FI",
                    f"PID eq {pid}",
                    "/FO",
                    "CSV",
                    "/NH",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await proc.communicate()
                if proc.returncode == 0 and stdout.strip():
                    pass
            except (OSError, FileNotFoundError):
                pass
        else:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "ps",
                    "-p",
                    str(pid),
                    "-o",
                    "command=",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await proc.communicate()
                if proc.returncode == 0:
                    cmd = stdout.decode().strip()
                    if (
                        "railclaw" in cmd.lower()
                        or "opencode" in cmd.lower()
                        or "gemini" in cmd.lower()
                    ):
                        issues.append(
                            RepairIssue(
                                severity=IssueSeverity.CRITICAL,
                                category="dangling_process",
                                description=f"Pipeline process PID {pid} is still running: {cmd[:100]}",
                                fixable=True,
                                fix_action="dangling_process",
                            )
                        )
            except (OSError, FileNotFoundError):
                pass

        return issues

    async def _fix_stale_lock(self) -> None:
        """Remove stale lock file."""
        with contextlib.suppress(FileNotFoundError, OSError):
            self.lock_path.unlink()

    async def _fix_orphaned_branch(self, branch: str) -> None:
        """Delete an orphaned branch locally."""
        try:
            await asyncio.create_subprocess_exec(
                "git",
                "-C",
                str(self.repo_path),
                "branch",
                "-D",
                branch,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (OSError, FileNotFoundError):
            pass

    async def _fix_uncommitted_changes(self) -> None:
        """Stash uncommitted changes."""
        try:
            await asyncio.create_subprocess_exec(
                "git",
                "-C",
                str(self.repo_path),
                "stash",
                "push",
                "-m",
                "pipeline-repair-stash",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (OSError, FileNotFoundError):
            pass

    async def _fix_corrupt_state(self) -> None:
        """Archive corrupt state file and create a clean template."""
        corrupt_dir = self.state_dir / "corrupt"
        corrupt_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        archive_path = corrupt_dir / f"state-{timestamp}.json.corrupt"
        try:
            shutil.copy2(self.state_path, archive_path)
        except OSError:
            pass

    async def _fix_dangling_process(self) -> None:
        """Kill the dangling pipeline process."""
        pid_path = self.state_dir / "pipeline.pid"
        try:
            content = pid_path.read_text().strip()
            pid = int(content.split("\n")[0])
        except (ValueError, OSError):
            return

        import signal

        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F", "/T"],
                    capture_output=True,
                    timeout=10,
                )
            else:
                os.kill(pid, signal.SIGTERM)
        except (OSError, FileNotFoundError, subprocess.TimeoutExpired):
            pass
