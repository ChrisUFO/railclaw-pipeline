"""Pre-flight validation gate — runs before Stage 0 to block bad pipeline runs."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from railclaw_pipeline.state.lock import StateLock


@dataclass
class PreflightFailure:
    """A single failed pre-flight check."""

    check: str
    message: str
    suggested_fix: str


@dataclass
class PreflightResult:
    """Result of running all pre-flight checks."""

    passed: bool
    failures: list[PreflightFailure] = field(default_factory=list)

    @property
    def failure_count(self) -> int:
        return len(self.failures)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "failure_count": self.failure_count,
            "failures": [
                {
                    "check": f.check,
                    "message": f.message,
                    "suggested_fix": f.suggested_fix,
                }
                for f in self.failures
            ],
        }


class PreflightGate:
    """Runs all mandatory checks before starting a pipeline run.

    All checks run regardless of individual failures — the result reports
    ALL failures at once so the user can fix everything in one pass.
    """

    def __init__(
        self,
        repo_path: Path,
        factory_path: Path,
        state_path: Path,
        lock_path: Path,
        lock_max_age: float = 14400,
        disk_space_min_mb: int = 500,
        agent_commands: list[str] | None = None,
    ) -> None:
        self.repo_path = repo_path
        self.factory_path = factory_path
        self.state_path = state_path
        self.lock_path = lock_path
        self.lock_max_age = lock_max_age
        self.disk_space_min_mb = disk_space_min_mb
        self.agent_commands = agent_commands or ["opencode --version", "gemini --version"]

    async def run(self) -> PreflightResult:
        """Run all pre-flight checks and return aggregated result."""
        failures: list[PreflightFailure] = []

        checks = [
            self._check_gh_auth,
            self._check_python_cli,
            self._check_agent_clis,
            self._check_repo,
            self._check_disk_space,
            self._check_state_dir_writable,
            self._check_no_active_lock,
        ]

        results = await asyncio.gather(
            *[check() for check in checks],
            return_exceptions=True,
        )

        for result in results:
            if isinstance(result, PreflightFailure):
                failures.append(result)
            elif isinstance(result, Exception):
                failures.append(
                    PreflightFailure(
                        check="unknown",
                        message=f"Unexpected error during check: {result}",
                        suggested_fix="Review pipeline logs for details.",
                    )
                )

        return PreflightResult(
            passed=len(failures) == 0,
            failures=failures,
        )

    async def _check_gh_auth(self) -> PreflightFailure | None:
        """Check 1: gh CLI authenticated and can access the repo."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "gh",
                "auth",
                "status",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                return PreflightFailure(
                    check="gh_auth",
                    message=f"GitHub CLI not authenticated: {stderr.decode().strip()}",
                    suggested_fix="Run 'gh auth login' to authenticate.",
                )
        except FileNotFoundError:
            return PreflightFailure(
                check="gh_auth",
                message="GitHub CLI (gh) not found on PATH.",
                suggested_fix="Install GitHub CLI: https://cli.github.com/",
            )
        return None

    async def _check_python_cli(self) -> PreflightFailure | None:
        """Check 2: Python venv exists and railclaw-pipeline CLI reachable."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "railclaw-pipeline",
                "--help",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            if proc.returncode != 0:
                return PreflightFailure(
                    check="python_cli",
                    message="railclaw-pipeline CLI returned non-zero exit code.",
                    suggested_fix="Ensure the package is installed: pip install -e python/",
                )
        except FileNotFoundError:
            return PreflightFailure(
                check="python_cli",
                message="railclaw-pipeline CLI not found on PATH.",
                suggested_fix="Activate your virtual environment and install the package.",
            )
        return None

    async def _check_agent_clis(self) -> PreflightFailure | None:
        """Check 3: All configured agent CLIs reachable."""
        missing = []
        for cmd_str in self.agent_commands:
            cmd_parts = cmd_str.split()
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd_parts,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()
                if proc.returncode != 0:
                    missing.append(cmd_str)
            except FileNotFoundError:
                missing.append(cmd_str)

        if missing:
            return PreflightFailure(
                check="agent_clis",
                message=f"Agent CLI(s) not reachable: {', '.join(missing)}",
                suggested_fix="Install the missing agent CLI(s) and ensure they are on PATH.",
            )
        return None

    async def _check_repo(self) -> PreflightFailure | None:
        """Check 4: Repo path exists, is a git repo, and has a clean working tree."""
        if not self.repo_path.exists():
            return PreflightFailure(
                check="repo",
                message=f"Repo path does not exist: {self.repo_path}",
                suggested_fix="Verify the repo path in your pipeline configuration.",
            )

        git_dir = self.repo_path / ".git"
        if not git_dir.exists():
            return PreflightFailure(
                check="repo",
                message=f"Not a git repository: {self.repo_path}",
                suggested_fix="Initialize a git repo or point to the correct repository.",
            )

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
                return PreflightFailure(
                    check="repo",
                    message="Working tree has uncommitted changes.",
                    suggested_fix="Commit or stash changes before running the pipeline.",
                )
        except FileNotFoundError:
            return PreflightFailure(
                check="repo",
                message="git command not found on PATH.",
                suggested_fix="Install git.",
            )

        return None

    async def _check_disk_space(self) -> PreflightFailure | None:
        """Check 5: Disk space > threshold (default 500MB)."""
        try:
            usage = shutil.disk_usage(self.factory_path)
            free_mb = usage.free / (1024 * 1024)
            if free_mb < self.disk_space_min_mb:
                return PreflightFailure(
                    check="disk_space",
                    message=f"Only {free_mb:.0f}MB free (minimum: {self.disk_space_min_mb}MB).",
                    suggested_fix="Free up disk space or increase the threshold in config.",
                )
        except OSError:
            return PreflightFailure(
                check="disk_space",
                message="Could not determine disk space for factory path.",
                suggested_fix="Verify the factory path exists and is accessible.",
            )
        return None

    async def _check_state_dir_writable(self) -> PreflightFailure | None:
        """Check 6: State directory is writable."""
        state_dir = self.state_path.parent
        try:
            state_dir.mkdir(parents=True, exist_ok=True)
            test_file = state_dir / ".write-test"
            test_file.touch()
            test_file.unlink()
        except OSError as exc:
            return PreflightFailure(
                check="state_dir",
                message=f"State directory not writable: {state_dir} ({exc})",
                suggested_fix="Check directory permissions.",
            )
        return None

    async def _check_no_active_lock(self) -> PreflightFailure | None:
        """Check 7: No active pipeline lock (uses StateLock stale detection)."""
        lock = StateLock(self.lock_path, max_age=self.lock_max_age)
        if lock.is_held():
            info = lock.get_info()
            lock_details = f"PID {info.pid}" if info else "unknown process"
            return PreflightFailure(
                check="active_lock",
                message=f"Pipeline lock is held by {lock_details}.",
                suggested_fix="Wait for the running pipeline to finish, or run 'railclaw-pipeline repair --fix'.",
            )
        return None
