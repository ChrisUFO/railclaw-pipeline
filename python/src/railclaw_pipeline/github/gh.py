"""GitHub CLI (gh) wrapper — shell=False with list args."""

from pathlib import Path
from typing import Any

from railclaw_pipeline.runner.subprocess_runner import SubprocessError, run_subprocess


class GhError(Exception):
    """Raised when a gh CLI command fails."""

    pass


class GhClient:
    """Wrapper around the gh CLI for GitHub API operations."""

    def __init__(self, repo_path: Path, timeout: float = 60) -> None:
        self.repo_path = repo_path
        self.timeout = timeout

    async def _gh(self, *args: str, timeout: float | None = None) -> str:
        """Run a gh command and return stdout."""
        cmd = ["gh"] + list(args)
        try:
            result = await run_subprocess(
                cmd,
                cwd=self.repo_path,
                timeout=timeout or self.timeout,
            )
            return result.stdout.strip()
        except SubprocessError as exc:
            raise GhError(f"gh command failed: {' '.join(cmd)}: {exc}") from exc

    async def is_authenticated(self) -> bool:
        """Check if gh is authenticated."""
        try:
            await self._gh("auth", "status", timeout=10)
            return True
        except GhError:
            return False

    async def issue_view(self, number: int) -> dict[str, Any]:
        """Get issue details as JSON."""
        output = await self._gh(
            "issue",
            "view",
            str(number),
            "--json",
            "title,body,labels,assignees,state",
        )
        import json

        return json.loads(output)

    async def issue_list(
        self,
        milestone: str | None = None,
        label: str | None = None,
        state: str = "open",
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        """List issues matching criteria."""
        args: list[str] = [
            "issue",
            "list",
            "--state",
            state,
            "--limit",
            str(limit),
            "--json",
            "number,title,body,labels",
        ]
        if milestone:
            args.extend(["--milestone", milestone])
        if label:
            args.extend(["--label", label])
        output = await self._gh(*args, timeout=self.timeout * 2)
        import json

        return json.loads(output)

    async def issue_create(
        self, title: str, body: str, labels: list[str] | None = None, assignee: str | None = None
    ) -> dict[str, Any]:
        """Create a new issue."""
        args: list[str] = ["issue", "create", "--title", title, "--body", body]
        if labels:
            args.extend(["--label", ",".join(labels)])
        if assignee:
            args.extend(["--assignee", assignee])
        output = await self._gh(*args, timeout=30)
        import json

        return json.loads(output) if output.startswith("{") else {"url": output.strip()}

    async def issue_comment(self, number: int, body: str) -> str:
        """Add a comment to an issue."""
        return await self._gh("issue", "comment", str(number), "--body", body, timeout=30)
