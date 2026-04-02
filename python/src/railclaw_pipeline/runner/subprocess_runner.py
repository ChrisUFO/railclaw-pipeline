"""Async subprocess wrappers for spawning agent processes.

All subprocess calls use shell=False with list arguments only.
"""

import asyncio
import shlex
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any


class SubprocessError(Exception):
    """Raised when a subprocess fails."""

    def __init__(self, message: str, returncode: int | None = None, stderr: str = "") -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr


class AgentVerdict(StrEnum):
    """Standardized verdict from agent execution."""
    PASS = "pass"
    REVISION = "revision"
    NEEDS_HUMAN = "needs-human"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass
class SubprocessResult:
    """Result from a subprocess execution."""
    stdout: str = ""
    stderr: str = ""
    returncode: int = -1
    duration: float = 0.0
    timed_out: bool = False
    killed: bool = False

    @property
    def success(self) -> bool:
        return self.returncode == 0 and not self.timed_out


def parse_verdict(stdout: str, stderr: str = "", returncode: int = 0) -> AgentVerdict:
    """Parse agent verdict from stdout/stderr output.

    Looks for RESULT_START/RESULT_END blocks or common verdict keywords.
    """
    # Check for structured result block
    if "RESULT_START" in stdout:
        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("status:"):
                status = line.split(":", 1)[1].strip()
                if status == "success":
                    return AgentVerdict.PASS
                elif status == "failure":
                    return AgentVerdict.REVISION
                elif status == "needs-human":
                    return AgentVerdict.NEEDS_HUMAN
                elif status == "timeout":
                    return AgentVerdict.TIMEOUT
                elif status == "error":
                    return AgentVerdict.ERROR

    # Fallback: keyword detection
    lower = stdout.lower()
    if any(kw in lower for kw in ["verdict: pass", "status: pass", "✓", "completed successfully"]):
        return AgentVerdict.PASS
    if any(kw in lower for kw in ["needs human", "blocked", "waiting for approval"]):
        return AgentVerdict.NEEDS_HUMAN
    if any(kw in lower for kw in ["revision needed", "changes requested", "fix required"]):
        return AgentVerdict.REVISION

    if returncode == 0:
        return AgentVerdict.PASS
    return AgentVerdict.ERROR


async def run_subprocess(
    command: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
    input_text: str | None = None,
) -> SubprocessResult:
    """Run a subprocess with timeout and output capture.

    Args:
        command: Command and arguments as a list (shell=False).
        cwd: Working directory.
        env: Additional environment variables.
        timeout: Maximum seconds to wait.
        input_text: Text to send to stdin.

    Returns:
        SubprocessResult with stdout, stderr, returncode, duration.

    Raises:
        SubprocessError: On timeout or non-zero exit with context.
    """
    if not command:
        raise SubprocessError("Empty command")

    start = datetime.now(timezone.utc)
    proc_env = None
    if env:
        import os
        proc_env = {**os.environ, **env}

    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE if input_text else asyncio.subprocess.DEVNULL,
            cwd=str(cwd) if cwd else None,
            env=proc_env,
        )
    except (OSError, FileNotFoundError) as exc:
        raise SubprocessError(
            f"Failed to start process: {command[0]!r}: {exc}"
        ) from exc

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(input=input_text.encode() if input_text else None),
            timeout=timeout,
        )
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        stdout_str = stdout_bytes.decode("utf-8", errors="replace")
        stderr_str = stderr_bytes.decode("utf-8", errors="replace")

        result = SubprocessResult(
            stdout=stdout_str,
            stderr=stderr_str,
            returncode=proc.returncode or 0,
            duration=elapsed,
        )

        if result.timed_out or not result.success:
            raise SubprocessError(
                f"Process exited with code {result.returncode}: {stderr_str[:500]}",
                returncode=result.returncode,
                stderr=stderr_str,
            )

        return result

    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()

        raise SubprocessError(
            f"Process timed out after {timeout}s: {command[0]!r}",
            returncode=-1,
        )


async def run_subprocess_safe(
    command: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
    input_text: str | None = None,
) -> SubprocessResult:
    """Like run_subprocess but returns result instead of raising on failure."""
    try:
        return await run_subprocess(command, cwd, env, timeout, input_text)
    except SubprocessError:
        elapsed = 0.0
        return SubprocessResult(
            stderr=f"Process failed: {' '.join(command)}",
            returncode=-1,
            timed_out=timeout is not None,
            duration=elapsed,
        )
