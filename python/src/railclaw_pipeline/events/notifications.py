"""Notification read/query/rotation for stage handoff payloads."""

import contextlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

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


def _rotate_notifications(path: Path) -> None:
    """Rotate notifications.jsonl at 10MB, keep 3 archives. Atomic via tempfile+fsync."""
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
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        suffix=".tmp",
        prefix="notifications_",
    )
    try:
        with os.fdopen(fd, "w") as tmp_file:
            tmp_file.write(path.read_text(encoding="utf-8"))
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, str(path.with_suffix(".jsonl.1")))
        path.unlink()
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


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
        try:
            naive = datetime.fromisoformat(since)
            cutoff = naive.replace(tzinfo=UTC)
        except ValueError:
            cutoff = datetime.fromisoformat(since.replace("Z", "+00:00")).astimezone(UTC)

    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if cutoff:
                    ts_str = data["ts"]
                    try:
                        ts_naive = datetime.fromisoformat(ts_str)
                        ts_aware = ts_naive if ts_naive.tzinfo else ts_naive.replace(tzinfo=UTC)
                    except ValueError:
                        ts_aware = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).astimezone(
                            UTC
                        )
                    if ts_aware < cutoff:
                        continue
                results.append(NotificationPayload(**data))
                if len(results) >= limit:
                    break
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                logger.warning("Skipping malformed notification line: %s", exc)
                continue

    return results


def write_notification(payload: NotificationPayload) -> None:
    """Append a notification atomically: tempfile → fsync → os.replace."""
    path = get_notifications_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _rotate_notifications(path)
    line = json.dumps(payload.__dict__, default=str) + "\n"

    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        suffix=".tmp",
        prefix="notif_",
    )
    try:
        with os.fdopen(fd, "w") as tmp_file:
            if path.exists():
                tmp_file.write(path.read_text(encoding="utf-8"))
            tmp_file.write(line)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, str(path))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise
