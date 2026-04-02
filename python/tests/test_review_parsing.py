"""Tests for review parsing and gemini polling helpers."""

from railclaw_pipeline.github.review import parse_details_blocks, ReviewFinding


def test_parse_details_blocks_empty():
    assert parse_details_blocks("") == []


def test_parse_details_blocks_single():
    body = (
        '<details><summary>High: Missing error handling</summary>\n'
        'The function does not handle null values.\n'
        '</details>'
    )
    findings = parse_details_blocks(body)
    assert len(findings) == 1
    assert findings[0].severity == "High"
    assert findings[0].title == "Missing error handling"
    assert "null values" in findings[0].description


def test_parse_details_blocks_multiple():
    body = (
        '<details><summary>High: Bug A</summary>\nDesc A\n</details>\n'
        '<details><summary>Medium: Suggestion B</summary>\nDesc B\n</details>\n'
        '<details><summary>Low: Polish C</summary>\nDesc C\n</details>'
    )
    findings = parse_details_blocks(body)
    assert len(findings) == 3
    assert findings[0].severity == "High"
    assert findings[1].severity == "Medium"
    assert findings[2].severity == "Low"


def test_parse_details_blocks_no_summary():
    body = '<details>\nSome content without summary\n</details>'
    findings = parse_details_blocks(body)
    assert len(findings) == 0


def test_review_finding_to_dict():
    finding = ReviewFinding(
        severity="HIGH",
        title="Test finding",
        description="Test description",
        category="completeness",
        file="test.py",
        line=42,
    )
    d = finding.to_dict()
    assert d["severity"] == "HIGH"
    assert d["title"] == "Test finding"
    assert d["file"] == "test.py"
    assert d["line"] == 42
