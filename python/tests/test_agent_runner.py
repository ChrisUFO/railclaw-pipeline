"""Tests for agent runner and subprocess execution."""

import asyncio
from pathlib import Path

import pytest

from railclaw_pipeline.runner.agent import AgentConfig, AgentResult
from railclaw_pipeline.runner.subprocess_runner import AgentVerdict, parse_verdict


def test_agent_config_build_args():
    config = AgentConfig(
        name="test",
        command="echo",
        args_template=["run", "--dir", "{dir}", "{prompt}"],
        workdir=Path("/tmp"),
    )
    args = config.build_args(Path("/work"))
    assert args == ["run", "--dir", "/work"]


def test_agent_config_model_substitution():
    config = AgentConfig(
        name="test",
        model="gpt-5",
        args_template=["--model", "{model}", "{prompt}"],
    )
    args = config.build_args(Path("/tmp"))
    assert args[0] == "--model"
    assert args[1] == "gpt-5"
    assert len(args) == 2


def test_parse_verdict_pass():
    assert parse_verdict("RESULT_START\nstatus: success\nRESULT_END") == AgentVerdict.PASS


def test_parse_verdict_revision():
    assert parse_verdict("RESULT_START\nstatus: failure\nRESULT_END") == AgentVerdict.REVISION


def test_parse_verdict_needs_human():
    assert parse_verdict("RESULT_START\nstatus: needs-human\nRESULT_END") == AgentVerdict.NEEDS_HUMAN


def test_parse_verdict_timeout():
    assert parse_verdict("RESULT_START\nstatus: timeout\nRESULT_END") == AgentVerdict.TIMEOUT


def test_parse_verdict_error():
    assert parse_verdict("RESULT_START\nstatus: error\nRESULT_END") == AgentVerdict.ERROR


def test_parse_verdict_keyword_pass():
    assert parse_verdict("verdict: pass - everything looks good") == AgentVerdict.PASS


def test_parse_verdict_keyword_needs_human():
    assert parse_verdict("This needs human review") == AgentVerdict.NEEDS_HUMAN


def test_parse_verdict_fallback_zero_exit():
    assert parse_verdict("some output", returncode=0) == AgentVerdict.PASS


def test_parse_verdict_fallback_nonzero_exit():
    assert parse_verdict("error output", returncode=1) == AgentVerdict.ERROR


def test_agent_result_success():
    result = AgentResult(
        agent_name="test",
        verdict=AgentVerdict.PASS,
        duration=1.5,
    )
    assert result.success is True


def test_agent_result_failure():
    result = AgentResult(
        agent_name="test",
        verdict=AgentVerdict.ERROR,
        error="something went wrong",
    )
    assert result.success is False


def test_agent_result_to_dict():
    result = AgentResult(
        agent_name="wrench",
        verdict=AgentVerdict.PASS,
        duration=2.0,
        returncode=0,
    )
    d = result.to_dict()
    assert d["agent"] == "wrench"
    assert d["verdict"] == "pass"
    assert d["duration"] == 2.0
