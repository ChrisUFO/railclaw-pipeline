"""Tests for Gemini review polling — cycle 2 helpers."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from railclaw_pipeline.config import PipelineConfig
from railclaw_pipeline.events.emitter import EventEmitter
from railclaw_pipeline.github.review import ReviewResult
from railclaw_pipeline.stages.cycle2_gemini import (
    _extract_gemini_findings,
    _parse_scope_findings,
    _parse_verdict,
)


def test_parse_verdict_pass():
    assert _parse_verdict("verdict: pass - all good") == "pass"


def test_parse_verdict_revision():
    assert _parse_verdict("verdict: revision needed") == "revision"


def test_parse_verdict_needs_human():
    assert _parse_verdict("verdict: needs-human - blocked") == "needs-human"


def test_parse_verdict_result_block_success():
    assert _parse_verdict("RESULT_START\nstatus: success\nRESULT_END") == "pass"


def test_parse_verdict_default_revision():
    assert _parse_verdict("random output without verdict") == "revision"


def test_parse_scope_findings_with_blocks():
    output = (
        "Review output\n"
        "FINDING_START\n"
        "severity: high\n"
        "description: missing tests\n"
        "FINDING_END\n"
        "more text\n"
        "FINDING_START\n"
        "severity: low\n"
        "description: style issue\n"
        "FINDING_END\n"
    )
    findings = _parse_scope_findings(output)
    assert len(findings) == 2
    assert findings[0]["severity"] == "high"
    assert findings[1]["description"] == "style issue"


def test_parse_scope_findings_empty():
    assert _parse_scope_findings("") == []
    assert _parse_scope_findings("no findings here") == []


def test_extract_gemini_findings_clean():
    result = ReviewResult(
        findings=[],
        is_clean=True,
        has_formal_review=True,
        raw_comments=[],
        raw_reviews=[],
    )
    findings = _extract_gemini_findings(result)
    assert findings == []


def test_extract_gemini_findings_from_reviews():
    result = ReviewResult(
        findings=[],
        is_clean=False,
        has_formal_review=True,
        raw_reviews=[
            {
                "body": '<details><summary>High: Bug</summary>\nFix this\n</details>',
                "state": "CHANGES_REQUESTED",
                "author": "gemini-bot",
            }
        ],
        raw_comments=[],
    )
    findings = _extract_gemini_findings(result)
    assert len(findings) >= 1


def test_extract_gemini_findings_from_comments():
    result = ReviewResult(
        findings=[],
        is_clean=False,
        has_formal_review=False,
        raw_reviews=[],
        raw_comments=[
            {"body": "Inline comment finding", "author": "gemini-bot"},
        ],
    )
    findings = _extract_gemini_findings(result)
    assert len(findings) == 1
    assert "Inline comment finding" in findings[0]["description"]


async def test_run_gemini_loop_no_pr_raises(tmp_path):
    from railclaw_pipeline.state.models import PipelineState, PipelineStage
    from railclaw_pipeline.stages.cycle2_gemini import run_gemini_loop

    config = PipelineConfig({
        "repoPath": str(tmp_path),
        "factoryPath": str(tmp_path / "factory"),
    })
    emitter = EventEmitter(tmp_path / "events.jsonl")
    state = PipelineState(issue_number=42, pr_number=None)
    runner = AsyncMock()

    with pytest.raises(RuntimeError, match="No PR number"):
        await run_gemini_loop(state, config, emitter, runner)
