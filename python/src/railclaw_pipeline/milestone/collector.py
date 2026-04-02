"""Milestone issue collector — gather issues from gh milestone."""

import logging
from typing import Any

from railclaw_pipeline.github.gh import GhClient
from pathlib import Path

logger = logging.getLogger(__name__)


async def collect_milestone_issues(
    repo_path: Path,
    milestone: str,
    state_filter: str = "open",
) -> list[dict[str, Any]]:
    """Collect all issues in a GitHub milestone.

    Args:
        repo_path: Path to the git repository.
        milestone: Milestone title or number.
        state_filter: Issue state filter (open, closed, all).

    Returns:
        List of issue dicts with number, title, body, labels.
    """
    gh = GhClient(repo_path)
    issues = await gh.issue_list(
        milestone=milestone,
        state=state_filter,
        limit=50,
    )

    if not issues:
        logger.warning("No issues found in milestone '%s'", milestone)
        return []

    logger.info(
        "Collected %d issues from milestone '%s'",
        len(issues),
        milestone,
    )
    return issues


def parse_plan_issues(plan_path: Path) -> list[int]:
    """Extract issue numbers from PLAN.md execution order.

    Looks for patterns like:
      ## Execution Order
      1. #65 — Fetch queue utility
      2. #72 — Rate limiter
    """
    import re

    if not plan_path.exists():
        return []

    content = plan_path.read_text(encoding="utf-8")

    numbers = []
    in_execution_section = False
    for line in content.splitlines():
        stripped = line.strip()

        if "execution order" in stripped.lower():
            in_execution_section = True
            continue

        if in_execution_section:
            if stripped.startswith("#") and not stripped.startswith("# "):
                break

            matches = re.findall(r"#(\d+)", stripped)
            for m in matches:
                num = int(m)
                if num not in numbers:
                    numbers.append(num)

            if not stripped:
                if numbers:
                    break

    return numbers
