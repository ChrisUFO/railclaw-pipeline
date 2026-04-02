"""Git operations wrapper — all calls use shell=False with list args."""

import re
from pathlib import Path

from railclaw_pipeline.runner.subprocess_runner import SubprocessError, run_subprocess


# Characters unsafe in branch names — sanitize before use in file paths or commands
UNSAFE_BRANCH_CHARS = re.compile(r"[^\w./-]")


def sanitize_branch_name(branch: str) -> str:
    """Remove characters unsafe for branch names and file paths."""
    return UNSAFE_BRANCH_CHARS.sub("_", branch)


class GitError(Exception):
    """Raised when a git operation fails."""
    pass


class GitOperations:
    """Safe git wrappers for pipeline operations."""

    def __init__(self, repo_path: Path, timeout: float = 120) -> None:
        self.repo_path = repo_path
        self.timeout = timeout

    async def _git(self, *args: str, timeout: float | None = None) -> str:
        """Run a git command and return stdout. Raises GitError on failure."""
        cmd = ["git"] + list(args)
        result = await run_subprocess(
            cmd,
            cwd=self.repo_path,
            timeout=timeout or self.timeout,
        )
        return result.stdout.strip()

    async def current_branch(self) -> str:
        """Get the current branch name."""
        return await self._git("rev-parse", "--abbrev-ref", "HEAD")

    async def is_dirty(self) -> bool:
        """Check if working tree has uncommitted changes."""
        result = await self._git("status", "--porcelain")
        return bool(result)

    async def checkout(self, branch: str) -> str:
        """Checkout a branch. Returns branch name."""
        await self._git("checkout", branch)
        return branch

    async def checkout_new(self, branch: str, base: str = "main") -> str:
        """Create and checkout a new branch from base."""
        await self._git("checkout", "-b", branch, base)
        return branch

    async def fetch(self, remote: str = "origin") -> str:
        """Fetch from remote."""
        return await self._git("fetch", remote)

    async def pull(self, remote: str = "origin", branch: str = "main") -> str:
        """Pull latest changes."""
        return await self._git("pull", remote, branch)

    async def push(
        self, remote: str = "origin", branch: str | None = None, set_upstream: bool = False
    ) -> str:
        """Push to remote."""
        args = ["push"]
        if set_upstream:
            args.extend(["-u", remote, branch or "HEAD"])
        else:
            args.append(remote)
            if branch:
                args.append(branch)
        return await self._git(*args, timeout=self.timeout * 2)

    async def branch_exists(self, branch: str, remote: str = "origin") -> bool:
        """Check if a branch exists on remote."""
        try:
            await self._git("rev-parse", "--verify", f"refs/remotes/{remote}/{branch}", timeout=10)
            return True
        except SubprocessError:
            return False

    async def delete_branch(self, branch: str, force: bool = False) -> None:
        """Delete a local branch."""
        flag = "-D" if force else "-d"
        try:
            await self._git("branch", flag, branch, timeout=10)
        except SubprocessError:
            pass  # Already deleted

    async def delete_remote_branch(self, branch: str, remote: str = "origin") -> None:
        """Delete a remote branch."""
        try:
            await self._git("push", remote, "--delete", branch, timeout=30)
        except SubprocessError:
            pass

    async def reset_hard(self, ref: str = "HEAD") -> str:
        """Reset working tree to ref."""
        return await self._git("reset", "--hard", ref)

    async def clean(self) -> str:
        """Remove untracked files and directories."""
        return await self._git("clean", "-fd")

    async def add(self, *paths: str) -> str:
        """Stage files."""
        return await self._git("add", *paths)

    async def commit(self, message: str) -> str:
        """Create a commit with the given message."""
        return await self._git("commit", "-m", message)

    async def log(self, count: int = 10, format_str: str = "%h %s") -> str:
        """Get commit log."""
        return await self._git("log", f"-{count}", f"--format={format_str}")

    async def ensure_clean(self) -> bool:
        """Ensure working tree is clean. Returns True if clean, raises if dirty."""
        if await self.is_dirty():
            raise GitError("Working tree has uncommitted changes")
        return True

    async def get_remote_url(self, remote: str = "origin") -> str:
        """Get the remote URL."""
        return await self._git("remote", "get-url", remote, timeout=10)
