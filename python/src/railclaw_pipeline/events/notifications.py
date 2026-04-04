"""Notification read/query/rotation for stage handoff payloads."""

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

MAX_NOTIFICATION_FILE_SIZE = 10 * 1024 * 1024  # 10MB
MAX_ROTATED_FILES = 3


@dataclass
class NotificationPayload:
    """Structured stage handoff notification."""

    ts: str
    type: str  # "stage_start" | "stage_end"
    issue: int
    stage: str
    duration_s: float | None = None
    verdict: str | None = None  # "pass" | "revision" | "needs-human" | "timeout" | "error"
    findings_count: int | None = None
    next_stage: str | None = None


def get_notifications_path() -> Path:
    """Return the notifications file path."""
    events_dir = os.environ.get("RAILCLAW_EVENTS_DIR", ".pipeline-events")
    factory_path = os.environ.get("RAILCLAW_FACTORY_PATH", "factory")
    return Path(factory_path) / events_dir / "notifications.jsonl"


def _rotate_notifications(path: Path) -> None:
    """Rotate notifications.jsonl at 10MB, keep 3 archives."""
    if not path.exists():
        return
    if path.stat().st_size < MAX_NOTIFICATION_FILE_SIZE:
        return
    for i in range(MAX_ROTATED_FILES, 0, -1):
        src = path.with_suffix(f".jsonl.{i}")
        if src.exists():
            if i == MAX_ROTATED_FILES:
                src.unlink()
            else:
                dst = path.with_suffix(f".jsonl.{i + 1}")
                src.rename(dst)
    path.rename(path.with_suffix(".jsonl.1"))


def query_notifications(
    since: str | None = None,
    limit: int = 100,
) -> list[NotificationPayload]:
    """Read notifications from file, optionally filtered by timestamp."""
    path = get_notifications_path()
    if not path.exists():
        return []

    results: list[NotificationPayload] = []
    cutoff: datetime | None = None
    if since:
        cutoff = datetime.fromisoformat(since.replace("Z", "+00:00"))

    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if cutoff:
                    ts = datetime.fromisoformat(data["ts"].replace("Z", "+00:00"))
                    if ts < cutoff:
                        continue
                results.append(NotificationPayload(**data))
                if len(results) >= limit:
                    break
            except (json.JSONDecodeError, KeyError, TypeError):
                continue

    return results


def write_notification(payload: NotificationPayload) -> None:
    """Append a notification to the notifications file, with rotation."""
    path = get_notifications_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _rotate_notifications(path)
    line = json.dumps(payload.__dict__, default=str)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
