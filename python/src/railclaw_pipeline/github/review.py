"""Review parsing and Gemini review polling."""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from railclaw_pipeline.github.pr import PrClient


@dataclass
class ReviewFinding:
    """A single finding from a code review."""

    file: str | None = None
    line: int | None = None
    severity: str = "info"
    category: str = "general"
    title: str = ""
    description: str = ""
    raw_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "line": self.line,
            "severity": self.severity,
            "category": self.category,
            "title": self.title,
            "description": self.description,
            "raw_text": self.raw_text,
        }


@dataclass
class ReviewResult:
    """Aggregated result from review parsing."""

    findings: list[ReviewFinding] = field(default_factory=list)
    is_clean: bool = True
    has_formal_review: bool = False
    last_processed_at: str | None = None
    raw_comments: list[dict[str, Any]] = field(default_factory=list)
    raw_reviews: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_clean": self.is_clean,
            "has_formal_review": self.has_formal_review,
            "finding_count": len(self.findings),
            "findings": [f.to_dict() for f in self.findings],
            "last_processed_at": self.last_processed_at,
        }


def parse_details_blocks(text: str) -> list[ReviewFinding]:
    """Extract ReviewFindings from <details>...</details> blocks in review body.

    Parses <summary>Severity: Title</summary> to populate severity and title.
    Blocks without a <summary> tag are skipped.
    """
    details_pattern = re.compile(r"<details[^>]*>(.*?)</details>", re.DOTALL)
    summary_pattern = re.compile(r"<summary[^>]*>(.*?)</summary>", re.DOTALL)
    findings: list[ReviewFinding] = []
    for match in details_pattern.finditer(text):
        block_content = match.group(1)
        summary_match = summary_pattern.search(block_content)
        if not summary_match:
            continue
        summary_text = summary_match.group(1).strip()
        if ": " in summary_text:
            severity, title = summary_text.split(": ", 1)
        else:
            severity = summary_text
            title = ""
        body = summary_pattern.sub("", block_content).strip()
        findings.append(
            ReviewFinding(
                severity=severity,
                title=title,
                description=body,
                raw_text=block_content.strip(),
            )
        )
    return findings


def classify_finding(text: str) -> tuple[str, str]:
    """Classify a finding by severity and category.

    Returns (severity, category).
    """
    lower = text.lower()
    # Severity
    if any(kw in lower for kw in ["critical", "security", "vulnerability", "xss", "injection"]):
        severity = "critical"
    elif any(kw in lower for kw in ["error", "bug", "broken", "must", "required"]):
        severity = "error"
    elif any(kw in lower for kw in ["warning", "should", "consider", "improve"]):
        severity = "warning"
    else:
        severity = "info"

    # Category
    if any(kw in lower for kw in ["test", "coverage", "spec"]):
        category = "testing"
    elif any(kw in lower for kw in ["security", "xss", "injection", "auth"]):
        category = "security"
    elif any(kw in lower for kw in ["type", "typescript", "python", "pydantic"]):
        category = "types"
    elif any(kw in lower for kw in ["error handling", "try", "except", "catch"]):
        category = "error-handling"
    elif any(kw in lower for kw in ["format", "style", "lint", "ruff", "prettier"]):
        category = "style"
    elif any(kw in lower for kw in ["doc", "comment", "readme"]):
        category = "documentation"
    else:
        category = "general"

    return severity, category


def extract_findings_from_comments(comments: list[dict[str, Any]]) -> list[ReviewFinding]:
    """Extract findings from PR inline comments."""
    findings = []
    for comment in comments:
        body = comment.get("body", "").strip()
        if not body:
            continue
        # Skip bot/system comments
        author = comment.get("author", "")
        if author in ("github-actions[bot]", "dependabot[bot]"):
            continue
        severity, category = classify_finding(body)
        findings.append(
            ReviewFinding(
                file=comment.get("path"),
                line=comment.get("line"),
                severity=severity,
                category=category,
                description=body[:500],
                raw_text=body,
            )
        )
    return findings


def extract_findings_from_reviews(reviews: list[dict[str, Any]]) -> list[ReviewFinding]:
    """Extract findings from PR review bodies and details blocks."""
    findings = []
    for review in reviews:
        state = review.get("state", "")
        body = review.get("body", "").strip()
        if not body:
            continue

        # Only process substantive reviews
        if state in ("COMMENTED", "CHANGES_REQUESTED", "APPROVED"):
            # Extract details blocks
            details_findings = parse_details_blocks(body)
            findings.extend(details_findings)

            # Also check for findings in non-details body
            # COMMENTED without <details> is informational — skip
            if not details_findings and state == "CHANGES_REQUESTED":
                severity, category = classify_finding(body)
                findings.append(
                    ReviewFinding(
                        severity=severity,
                        category=category,
                        description=body[:500],
                        raw_text=body,
                    )
                )

    return findings


async def poll_reviews(
    pr_client: PrClient,
    pr_number: int,
    last_processed_at: str | None = None,
) -> ReviewResult:
    """Poll PR comments and reviews for new findings.

    Args:
        pr_client: PR client instance.
        pr_number: PR number to poll.
        last_processed_at: ISO timestamp to filter old findings.

    Returns:
        ReviewResult with aggregated findings.
    """
    comments = await pr_client.comments(pr_number)
    reviews = await pr_client.reviews(pr_number)

    # Filter by timestamp if provided
    if last_processed_at:
        try:
            cutoff = datetime.fromisoformat(last_processed_at.replace("Z", "+00:00"))
            comments = [
                c
                for c in comments
                if datetime.fromisoformat(c.get("createdAt", "").replace("Z", "+00:00")) > cutoff
            ]
            reviews = [
                r
                for r in reviews
                if datetime.fromisoformat(r.get("submittedAt", "").replace("Z", "+00:00")) > cutoff
            ]
        except (ValueError, TypeError):
            pass  # If parsing fails, process all

    findings = extract_findings_from_comments(comments)
    findings.extend(extract_findings_from_reviews(reviews))

    has_formal = any(
        r.get("state") in ("APPROVED", "CHANGES_REQUESTED", "COMMENTED") for r in reviews
    )

    return ReviewResult(
        findings=findings,
        is_clean=len(findings) == 0,
        has_formal_review=has_formal,
        last_processed_at=datetime.now(UTC).isoformat(),
        raw_comments=comments,
        raw_reviews=reviews,
    )
