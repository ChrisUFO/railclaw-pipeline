"""Tests for notification read/write/query."""

import json
import os
import tempfile
from pathlib import Path

import pytest

from railclaw_pipeline.events.notifications import (
    MAX_ROTATED_FILES,
    NotificationPayload,
    get_notifications_path,
    query_notifications,
    write_notification,
)


@pytest.fixture
def temp_factory(tmp_path):
    """Set up temp factory env."""
    events_dir = tmp_path / ".pipeline-events"
    events_dir.mkdir()
    os.environ["RAILCLAW_FACTORY_PATH"] = str(tmp_path)
    os.environ["RAILCLAW_EVENTS_DIR"] = ".pipeline-events"
    yield tmp_path
    for key in ["RAILCLAW_FACTORY_PATH", "RAILCLAW_EVENTS_DIR"]:
        os.environ.pop(key, None)


class TestNotificationPayload:
    def test_dataclass_fields(self):
        payload = NotificationPayload(
            ts="2024-01-01T00:00:00Z",
            type="stage_end",
            issue=42,
            stage="stage1_blueprint",
            duration_s=120.5,
            verdict="pass",
            findings_count=3,
            next_stage="stage2_wrench",
        )
        assert payload.ts == "2024-01-01T00:00:00Z"
        assert payload.type == "stage_end"
        assert payload.issue == 42
        assert payload.stage == "stage1_blueprint"
        assert payload.duration_s == 120.5
        assert payload.verdict == "pass"
        assert payload.findings_count == 3
        assert payload.next_stage == "stage2_wrench"


class TestWriteNotification:
    def test_write_single_notification(self, temp_factory):
        payload = NotificationPayload(
            ts="2024-01-01T00:00:00Z",
            type="stage_start",
            issue=42,
            stage="stage1_blueprint",
        )
        write_notification(payload)
        path = get_notifications_path()
        assert path.exists()
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["type"] == "stage_start"
        assert data["issue"] == 42

    def test_write_creates_parent_dirs(self, temp_factory):
        payload = NotificationPayload(
            ts="2024-01-01T00:00:00Z",
            type="stage_start",
            issue=1,
            stage="stage0_preflight",
        )
        write_notification(payload)
        path = get_notifications_path()
        assert path.parent.exists()


class TestQueryNotifications:
    def test_query_empty(self, temp_factory):
        results = query_notifications()
        assert results == []

    def test_query_returns_all(self, temp_factory):
        for i in range(3):
            payload = NotificationPayload(
                ts=f"2024-01-01T00:00:{i:02d}Z",
                type="stage_end",
                issue=i,
                stage=f"stage{i}",
            )
            write_notification(payload)
        results = query_notifications()
        assert len(results) == 3

    def test_query_since_filter(self, temp_factory):
        for i in range(3):
            payload = NotificationPayload(
                ts=f"2024-01-01T00:00:{i:02d}Z",
                type="stage_end",
                issue=i,
                stage=f"stage{i}",
            )
            write_notification(payload)
        results = query_notifications(since="2024-01-01T00:00:01Z")
        assert len(results) == 2
        assert all(n.issue >= 1 for n in results)

    def test_query_limit(self, temp_factory):
        for i in range(5):
            payload = NotificationPayload(
                ts=f"2024-01-01T00:00:{i:02d}Z",
                type="stage_end",
                issue=i,
                stage=f"stage{i}",
            )
            write_notification(payload)
        results = query_notifications(limit=2)
        assert len(results) == 2


class TestNotificationRotation:
    def test_rotation_creates_archive(self, temp_factory, monkeypatch):
        import railclaw_pipeline.events.notifications as notif_mod

        monkeypatch.setattr(notif_mod, "MAX_NOTIFICATION_FILE_SIZE", 10)

        path = get_notifications_path()
        for i in range(5):
            payload = NotificationPayload(
                ts=f"2024-01-01T00:00:{i:02d}Z",
                type="stage_end",
                issue=i,
                stage=f"stage{i}",
            )
            write_notification(payload)

        archive = path.with_suffix(".jsonl.1")
        assert archive.exists() or path.exists()

    def test_max_rotated_files_preserved(self, temp_factory, monkeypatch):
        import railclaw_pipeline.events.notifications as notif_mod

        monkeypatch.setattr(notif_mod, "MAX_NOTIFICATION_FILE_SIZE", 10)
        monkeypatch.setattr(notif_mod, "MAX_ROTATED_FILES", 2)

        path = get_notifications_path()
        for i in range(10):
            payload = NotificationPayload(
                ts=f"2024-01-01T00:00:{i:02d}Z",
                type="stage_end",
                issue=i,
                stage=f"stage{i}",
            )
            write_notification(payload)

        archives = list(path.parent.glob("notifications.jsonl.*"))
        assert len(archives) <= 2
