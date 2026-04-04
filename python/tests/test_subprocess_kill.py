"""Tests for subprocess kill cascade — SIGTERM→SIGKILL on timeout."""

import asyncio
import sys
import time
from pathlib import Path

import pytest

from railclaw_pipeline.runner.subprocess_runner import (
    SubprocessError,
    SubprocessResult,
    run_subprocess,
    run_subprocess_safe,
)


class TestSubprocessKillCascade:
    async def test_slow_process_gets_killed_on_timeout(self) -> None:
        cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
        start = time.monotonic()
        with pytest.raises(SubprocessError, match="timed out"):
            await run_subprocess(cmd, timeout=0.5)
        elapsed = time.monotonic() - start
        assert elapsed < 15

    async def test_process_completes_before_timeout(self) -> None:
        cmd = [sys.executable, "-c", "print('done')"]
        result = await run_subprocess(cmd, timeout=10)
        assert result.success
        assert "done" in result.stdout

    async def test_safe_returns_result_on_timeout(self) -> None:
        cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
        result = await run_subprocess_safe(cmd, timeout=0.5)
        assert not result.success
        assert result.returncode == -1

    async def test_timeout_does_not_propagate(self) -> None:
        cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
        try:
            await run_subprocess(cmd, timeout=0.3)
        except SubprocessError:
            pass
        result = await run_subprocess([sys.executable, "-c", "print('ok')"], timeout=5)
        assert result.success
