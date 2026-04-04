"""Tests for milestone mode — collection and plan parsing."""

from pathlib import Path

import pytest

from railclaw_pipeline.milestone.collector import parse_plan_issues


def test_parse_plan_issues_with_execution_order(tmp_path):
    plan = tmp_path / "PLAN.md"
    plan.write_text(
        """# Plan

## Execution Order
1. #65 — Fetch queue utility
2. #72 — Rate limiter
3. #78 — Cache invalidation

## Phase 1
Some content here
""",
        encoding="utf-8",
    )
    result = parse_plan_issues(plan)
    assert result == [65, 72, 78]


def test_parse_plan_issues_no_file(tmp_path):
    plan = tmp_path / "nonexistent.md"
    result = parse_plan_issues(plan)
    assert result == []


def test_parse_plan_issues_no_execution_order(tmp_path):
    plan = tmp_path / "PLAN.md"
    plan.write_text("# Plan\n\nJust a plan without execution order.\n")
    result = parse_plan_issues(plan)
    assert result == []


def test_parse_plan_issues_deduplication(tmp_path):
    plan = tmp_path / "PLAN.md"
    plan.write_text(
        """## Execution Order
1. #42 — First
2. #42 — Duplicate
3. #99 — Third
""",
        encoding="utf-8",
    )
    result = parse_plan_issues(plan)
    assert result == [42, 99]
