"""Tests for review parsing — COMMENTED handling in poll_reviews and extract_findings."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from railclaw_pipeline.github.review import (
    ReviewResult,
    extract_findings_from_reviews,
    poll_reviews,
)


def test_commented_sets_formal_review():
    """COMMENTED review without <details>: has_formal_review=True, is_clean=True."""
    reviews = [
        {
            "body": "The code looks good overall. Nice work.",
            "state": "COMMENTED",
            "submittedAt": "2026-04-03T10:00:00Z",
        }
    ]
    findings = extract_findings_from_reviews(reviews)
    has_formal = any(r.get("state") in ("APPROVED", "CHANGES_REQUESTED", "COMMENTED") for r in reviews)

    result = ReviewResult(
        findings=findings,
        is_clean=len(findings) == 0,
        has_formal_review=has_formal,
    )
    assert result.has_formal_review is True
    assert result.is_clean is True


def test_commented_with_details_has_findings():
    """COMMENTED with <details> blocks: has_formal_review=True, is_clean=False."""
    reviews = [
        {
            "body": '<details><summary>High: Missing error handling</summary>\nThe function does not handle null values.\n</details>',
            "state": "COMMENTED",
            "submittedAt": "2026-04-03T10:00:00Z",
        }
    ]
    findings = extract_findings_from_reviews(reviews)
    has_formal = any(r.get("state") in ("APPROVED", "CHANGES_REQUESTED", "COMMENTED") for r in reviews)

    result = ReviewResult(
        findings=findings,
        is_clean=len(findings) == 0,
        has_formal_review=has_formal,
    )
    assert result.has_formal_review is True
    assert result.is_clean is False
    assert len(result.findings) > 0


def test_changes_requested_without_details_creates_finding():
    """CHANGES_REQUESTED without <details> should create finding from body."""
    reviews = [
        {
            "body": "This function has a critical bug that must be fixed.",
            "state": "CHANGES_REQUESTED",
            "submittedAt": "2026-04-03T10:00:00Z",
        }
    ]
    findings = extract_findings_from_reviews(reviews)
    assert len(findings) == 1


def test_extract_findings_commented_no_details_no_finding():
    """COMMENTED without <details> should not create a finding (informational)."""
    reviews = [
        {
            "body": "LGTM, looks good to me.",
            "state": "COMMENTED",
        }
    ]
    findings = extract_findings_from_reviews(reviews)
    assert len(findings) == 0


@pytest.mark.asyncio
async def test_poll_reviews_commented_no_details():
    """poll_reviews() async: COMMENTED with no <details> → has_formal=True, findings=[]."""
    mock_client = MagicMock()
    mock_client.comments = AsyncMock(return_value=[])
    mock_client.reviews = AsyncMock(return_value=[
        {
            "body": "The code looks good overall. Nice work.",
            "state": "COMMENTED",
            "submittedAt": "2026-04-03T10:00:00Z",
        }
    ])

    result = await poll_reviews(mock_client, pr_number=42)

    assert result.has_formal_review is True
    assert result.findings == []
    assert result.is_clean is True
