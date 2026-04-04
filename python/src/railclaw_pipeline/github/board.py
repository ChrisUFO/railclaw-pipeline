"""Board JSON helpers — read/update factory/board.json."""

import contextlib
import json
from pathlib import Path
from typing import Any


class BoardError(Exception):
    """Raised when board operations fail."""
    pass


def load_board(factory_path: Path) -> dict[str, Any]:
    """Load board.json from factory directory.

    Returns empty dict if file doesn't exist.
    """
    board_path = factory_path / "board.json"
    if not board_path.exists():
        return {}
    try:
        data = json.loads(board_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        raise BoardError(f"Failed to load board.json: {exc}") from exc


def save_board(factory_path: Path, board: dict[str, Any]) -> None:
    """Atomically save board.json."""
    board_path = factory_path / "board.json"
    board_path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(board, indent=2, ensure_ascii=False)

    import os
    import tempfile
    fd, tmp_path = tempfile.mkstemp(dir=str(board_path.parent), suffix=".tmp", prefix="board_")
    try:
        os.write(fd, data.encode("utf-8"))
        os.fsync(fd)
        os.close(fd)
        os.replace(tmp_path, str(board_path))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


def update_issue_status(
    factory_path: Path,
    issue_number: int,
    status: str,
    stage: str | None = None,
    pr_number: int | None = None,
    assignee: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Update an issue's status on the board.

    Args:
        factory_path: Path to factory directory.
        issue_number: GitHub issue number.
        status: New status (e.g., "in-progress", "review", "completed").
        stage: Current pipeline stage.
        pr_number: Associated PR number.
        assignee: Agent or person assigned.
        notes: Additional notes.

    Returns:
        Updated board data.
    """
    board = load_board(factory_path)
    issues = board.get("issues", {})
    key = str(issue_number)

    entry = issues.get(key, {"number": issue_number})
    entry["status"] = status
    if stage:
        entry["stage"] = stage
    if pr_number is not None:
        entry["pr"] = pr_number
    if assignee:
        entry["assignee"] = assignee
    if notes:
        entry["notes"] = notes

    issues[key] = entry
    board["issues"] = issues
    board["lastUpdated"] = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    ).isoformat()

    save_board(factory_path, board)
    return board


def get_issue_entry(factory_path: Path, issue_number: int) -> dict[str, Any] | None:
    """Get a specific issue entry from the board."""
    board = load_board(factory_path)
    return board.get("issues", {}).get(str(issue_number))
