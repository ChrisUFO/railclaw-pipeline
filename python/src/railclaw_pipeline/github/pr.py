"""PR operations using gh CLI."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from railclaw_pipeline.github.gh import GhClient, GhError


class PrError(Exception):
    """Raised when a PR operation fails."""
    pass


class PrClient:
    """PR management via gh CLI."""

    def __init__(self, repo_path: Path, timeout: float = 60) -> None:
        self.gh = GhClient(repo_path, timeout)

    async def create(
        self,
        title: str,
        body: str,
        base: str = "main",
        head: str | None = None,
        draft: bool = False,
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a pull request.

        Args:
            title: PR title.
            body: PR body (supports markdown).
            base: Target branch.
            head: Source branch (defaults to current).
            draft: Create as draft PR.
            labels: Labels to add.

        Returns:
            Dict with pr_number, url, etc.
        """
        args: list[str] = [
            "pr", "create",
            "--title", title,
            "--body", body,
            "--base", base,
        ]
        if head:
            args.extend(["--head", head])
        if draft:
            args.append("--draft")
        if labels:
            args.extend(["--label", ",".join(labels)])

        try:
            output = await self.gh._gh(*args, timeout=60)
            result: dict[str, Any] = {"url": output.strip()}
            if "/pull/" in output:
                match = re.search(r"/pull/(\d+)", output)
                if not match:
                    raise PrError(f"Failed to extract PR number from URL: {output.strip()}")
                result["pr_number"] = int(match.group(1))
            return result
        except GhError as exc:
            raise PrError(f"Failed to create PR: {exc}") from exc

    async def view(self, number: int, json_fields: str = "number,title,state,mergeable,headRefName") -> dict[str, Any]:
        """View PR details."""
        try:
            output = await self.gh._gh("pr", "view", str(number), "--json", json_fields, timeout=30)
            return json.loads(output)
        except GhError as exc:
            raise PrError(f"Failed to view PR #{number}: {exc}") from exc

    async def is_mergeable(self, number: int) -> tuple[bool, str]:
        """Check if PR is mergeable. Returns (mergeable, state_string)."""
        try:
            data = await self.view(number, json_fields="number,state,mergeable,mergeStateStatus")
            mergeable = data.get("mergeable", False)
            state = data.get("mergeStateStatus", "UNKNOWN")
            return mergeable, state
        except GhError:
            return False, "UNKNOWN"

    async def merge(self, number: int, merge_method: str = "squash") -> str:
        """Merge a PR. Returns merge commit SHA or URL."""
        try:
            return await self.gh._gh(
                "pr", "merge", str(number),
                "--merge", merge_method,
                "--delete-branch",
                timeout=60,
            )
        except GhError as exc:
            raise PrError(f"Failed to merge PR #{number}: {exc}") from exc

    async def comment(self, number: int, body: str) -> str:
        """Add a comment to a PR."""
        return await self.gh._gh("pr", "comment", str(number), "--body", body, timeout=30)

    async def list(
        self,
        state: str = "open",
        head: str | None = None,
        base: str = "main",
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """List PRs."""
        args: list[str] = [
            "pr", "list", "--state", state, "--base", base,
            "--limit", str(limit),
            "--json", "number,title,headRefName,state,mergeable",
        ]
        if head:
            args.extend(["--head", head])
        try:
            output = await self.gh._gh(*args, timeout=30)
            return json.loads(output)
        except GhError:
            return []

    async def comments(self, number: int) -> list[dict[str, Any]]:
        """Get PR comments."""
        try:
            output = await self.gh._gh(
                "pr", "view", str(number),
                "--json", "comments",
                "--jq", ".comments[] | {author: .author.login, body: .body, createdAt: .createdAt, path: .path, line: .line}",
                timeout=30,
            )
            if not output.strip():
                return []
            return [json.loads(line) for line in output.strip().split("\n") if line.strip()]
        except (GhError, json.JSONDecodeError):
            return []

    async def reviews(self, number: int) -> list[dict[str, Any]]:
        """Get PR reviews."""
        try:
            output = await self.gh._gh(
                "pr", "view", str(number),
                "--json", "reviews",
                "--jq", '.reviews[] | {author: .author.login, state: .state, body: .body, submittedAt: .submittedAt}',
                timeout=30,
            )
            if not output.strip():
                return []
            return [json.loads(line) for line in output.strip().split("\n") if line.strip()]
        except (GhError, json.JSONDecodeError):
            return []

    async def find_by_head(self, head_branch: str) -> dict[str, Any] | None:
        """Find an open PR by head branch name."""
        prs = await self.list(state="open", head=head_branch)
        return prs[0] if prs else None
