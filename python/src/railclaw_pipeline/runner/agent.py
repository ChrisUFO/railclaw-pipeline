"""Agent configuration and execution for coding agents.

Supports opencode, gemini, and other CLI-based coding agents.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from railclaw_pipeline.runner.subprocess_runner import (
    AgentVerdict,
    SubprocessResult,
    parse_verdict,
    run_subprocess,
)


@dataclass
class AgentConfig:
    """Configuration for a coding agent."""
    name: str
    model: str = ""
    timeout: int = 600
    command: str = "opencode"
    args_template: list[str] = field(default_factory=lambda: ["run", "--dir", "{dir}", "{prompt}"])
    workdir: Path | None = None

    def build_args(self, workdir: Path) -> list[str]:
        """Build command arguments with template substitution.

        Prompt is passed via stdin to avoid ARG_MAX limits.
        """
        args = []
        for arg in self.args_template:
            if arg == "{prompt}":
                continue
            arg = arg.replace("{dir}", str(workdir))
            arg = arg.replace("{model}", self.model)
            args.append(arg)
        return args


@dataclass
class AgentResult:
    """Result from an agent execution."""
    agent_name: str
    verdict: AgentVerdict
    stdout: str = ""
    stderr: str = ""
    duration: float = 0.0
    returncode: int = 0
    timed_out: bool = False
    error: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None

    @property
    def success(self) -> bool:
        return self.verdict == AgentVerdict.PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent_name,
            "verdict": self.verdict.value,
            "duration": self.duration,
            "returncode": self.returncode,
            "timed_out": self.timed_out,
            "error": self.error,
            "stdout_preview": self.stdout[:1000] if self.stdout else None,
        }


class AgentRunner:
    """Executes coding agents via subprocess."""

    def __init__(
        self,
        agent: AgentConfig,
        workdir: Path,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        self.agent = agent
        self.workdir = workdir
        self.extra_env = extra_env or {}
        self._process = None

    async def run(self, prompt: str, timeout: int | None = None) -> AgentResult:
        """Execute the agent with the given prompt.

        Args:
            prompt: The prompt/task to send to the agent.
            timeout: Override timeout in seconds.

        Returns:
            AgentResult with verdict and output.
        """
        effective_timeout = timeout or self.agent.timeout
        workdir = self.agent.workdir or self.workdir
        args = self.agent.build_args(workdir)

        command = [self.agent.command] + args
        started = datetime.now(timezone.utc)

        try:
            result: SubprocessResult = await run_subprocess(
                command,
                cwd=workdir,
                env=self.extra_env,
                timeout=effective_timeout,
                input_text=prompt,
            )
            finished = datetime.now(timezone.utc)
            verdict = parse_verdict(result.stdout, result.stderr, result.returncode)

            return AgentResult(
                agent_name=self.agent.name,
                verdict=verdict,
                stdout=result.stdout,
                stderr=result.stderr,
                duration=result.duration,
                returncode=result.returncode,
                timed_out=result.timed_out,
                started_at=started,
                finished_at=finished,
            )

        except (SystemExit, KeyboardInterrupt):
            raise
        except Exception as exc:
            finished = datetime.now(timezone.utc)
            return AgentResult(
                agent_name=self.agent.name,
                verdict=AgentVerdict.ERROR,
                duration=(finished - started).total_seconds(),
                error=str(exc),
                started_at=started,
                finished_at=finished,
            )

    async def kill(self) -> None:
        """Kill a running agent process."""
        if self._process:
            try:
                self._process.kill()
            except ProcessLookupError:
                pass
            self._process = None
