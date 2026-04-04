"""Notification read/query/rotation for stage handoff payloads."""

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from railclaw_pipeline.utils.rotation import rotate_jsonl

logger = logging.getLogger(__name__)

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


def query_notifications(
    since: str | None = None,
    limit: int = 100,
) -> list[NotificationPayload]:
    """Read notifications from file, returning newest-first for polling consumers."""
    path = get_notifications_path()
    if not path.exists():
        return []

    all_entries: list[NotificationPayload] = []
    cutoff: datetime | None = None
    if since:
        try:
            dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            cutoff = dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            pass

    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if cutoff:
                    try:
                        ts_aware = datetime.fromisoformat(data["ts"])
                        if not ts_aware.tzinfo:
                            ts_aware = ts_aware.replace(tzinfo=UTC)
                    except ValueError:
                        continue
                    if ts_aware < cutoff:
                        continue
                valid_fields = {
                    k: v for k, v in data.items() if k in NotificationPayload.__dataclass_fields__
                }
                all_entries.append(NotificationPayload(**valid_fields))
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                logger.warning("Skipping malformed notification line: %s", exc)
                continue

    all_entries.reverse()
    return all_entries[:limit]


def write_notification(payload: NotificationPayload) -> None:
    """Append a notification atomically: open → write → fsync → close."""
    path = get_notifications_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    rotate_jsonl(path, MAX_NOTIFICATION_FILE_SIZE, MAX_ROTATED_FILES)
    line = json.dumps(asdict(payload), default=str) + "\n"

    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
