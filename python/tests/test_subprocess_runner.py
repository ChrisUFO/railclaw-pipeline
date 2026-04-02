"""Tests for subprocess runner — async subprocess wrappers."""

import asyncio

import pytest

from railclaw_pipeline.runner.subprocess_runner import (
    AgentVerdict,
    SubprocessError,
    SubprocessResult,
    parse_verdict,
    run_subprocess,
    run_subprocess_safe,
)


def test_subprocess_result_defaults():
    result = SubprocessResult()
    assert result.stdout == ""
    assert result.stderr == ""
    assert result.returncode == -1
    assert result.duration == 0.0
    assert result.timed_out is False
    assert result.killed is False
    assert result.success is False


def test_subprocess_result_success():
    result = SubprocessResult(returncode=0)
    assert result.success is True


def test_subprocess_result_timeout_not_success():
    result = SubprocessResult(returncode=0, timed_out=True)
    assert result.success is False


async def test_run_subprocess_echo():
    result = await run_subprocess(["echo", "hello"], timeout=10)
    assert result.stdout.strip() == "hello"
    assert result.returncode == 0


async def test_run_subprocess_cwd(tmp_path):
    (tmp_path / "test.txt").write_text("found")
    result = await run_subprocess(["cat", "test.txt"], cwd=tmp_path, timeout=10)
    assert result.stdout.strip() == "found"


async def test_run_subprocess_timeout():
    with pytest.raises(SubprocessError, match="timed out"):
        await run_subprocess(["sleep", "10"], timeout=0.1)


async def test_run_subprocess_nonzero_exit():
    with pytest.raises(SubprocessError):
        await run_subprocess(["false"], timeout=10)


async def test_run_subprocess_empty_command():
    with pytest.raises(SubprocessError, match="Empty command"):
        await run_subprocess([])


async def test_run_subprocess_missing_binary():
    with pytest.raises(SubprocessError, match="Failed to start"):
        await run_subprocess(["nonexistent_binary_xyz_123"], timeout=5)


async def test_run_subprocess_safe_returns_result():
    result = await run_subprocess_safe(["echo", "safe"], timeout=10)
    assert result.stdout.strip() == "safe"


async def test_run_subprocess_safe_handles_failure():
    result = await run_subprocess_safe(["false"], timeout=10)
    assert result.returncode == -1


async def test_run_subprocess_with_env():
    result = await run_subprocess(
        ["env"],
        env={"RAILCLAW_TEST_VAR": "test_value_123"},
        timeout=10,
    )
    assert "test_value_123" in result.stdout


async def test_run_subprocess_with_input():
    result = await run_subprocess(
        ["cat"],
        input_text="hello from stdin",
        timeout=10,
    )
    assert "hello from stdin" in result.stdout


def test_parse_verdict_all_status_types():
    assert parse_verdict("RESULT_START\nstatus: success\nRESULT_END") == AgentVerdict.PASS
    assert parse_verdict("RESULT_START\nstatus: failure\nRESULT_END") == AgentVerdict.REVISION
    assert parse_verdict("RESULT_START\nstatus: needs-human\nRESULT_END") == AgentVerdict.NEEDS_HUMAN
    assert parse_verdict("RESULT_START\nstatus: timeout\nRESULT_END") == AgentVerdict.TIMEOUT
    assert parse_verdict("RESULT_START\nstatus: error\nRESULT_END") == AgentVerdict.ERROR
