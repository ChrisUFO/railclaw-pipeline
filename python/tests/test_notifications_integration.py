"""Integration tests for notification read/query and rotation."""

import json
import os
import subprocess
import sys

import pytest


@pytest.fixture
def temp_factory_env(tmp_path):
    """Set up minimal factory environment for notification tests."""
    factory = tmp_path / "factory"
    events_dir = factory / ".pipeline-events"
    state_dir = factory / ".pipeline-state"

    events_dir.mkdir(parents=True)
    state_dir.mkdir(parents=True)

    old_events = os.environ.get("RAILCLAW_EVENTS_DIR")
    old_factory = os.environ.get("RAILCLAW_FACTORY_PATH")
    old_state = os.environ.get("RAILCLAW_STATE_DIR")

    os.environ["RAILCLAW_EVENTS_DIR"] = ".pipeline-events"
    os.environ["RAILCLAW_FACTORY_PATH"] = str(factory)
    os.environ["RAILCLAW_STATE_DIR"] = ".pipeline-state"

    yield {
        "factory": factory,
        "events_dir": events_dir,
        "state_dir": state_dir,
        "notifications_path": events_dir / "notifications.jsonl",
    }

    if old_events:
        os.environ["RAILCLAW_EVENTS_DIR"] = old_events
    if old_factory:
        os.environ["RAILCLAW_FACTORY_PATH"] = old_factory
    if old_state:
        os.environ["RAILCLAW_STATE_DIR"] = old_state


class TestNotificationsCommand:
    def test_notifications_command_exists_and_returns_ok(self, temp_factory_env):
        """railclaw-pipeline notifications returns ok with empty list when no notifications."""
        result = subprocess.run(
            [sys.executable, "-m", "railclaw_pipeline.cli", "notifications"],
            capture_output=True,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout.decode().strip())
        assert data["ok"] is True
        assert data["action"] == "notifications"
        assert data["count"] == 0
        assert data["notifications"] == []

    def test_notifications_returns_written_notifications(self, temp_factory_env):
        """railclaw-pipeline notifications returns notifications written directly to file."""
        notifications_path = temp_factory_env["notifications_path"]
        notifications_path.parent.mkdir(parents=True, exist_ok=True)

        ts1 = "2024-01-01T00:00:00Z"
        ts2 = "2024-01-01T00:01:00Z"
        notif1 = {"ts": ts1, "type": "stage_start", "issue": 1, "stage": "stage1_blueprint"}
        notif2 = {
            "ts": ts2,
            "type": "stage_end",
            "issue": 1,
            "stage": "stage1_blueprint",
            "duration_s": 60.0,
            "verdict": "pass",
            "findings_count": 0,
        }

        notifications_path.write_text(json.dumps(notif1) + "\n" + json.dumps(notif2) + "\n")

        result = subprocess.run(
            [sys.executable, "-m", "railclaw_pipeline.cli", "notifications"],
            capture_output=True,
        )
        data = json.loads(result.stdout.decode().strip())
        assert data["count"] == 2
        assert data["notifications"][0]["type"] == "stage_end"
        assert data["notifications"][1]["type"] == "stage_start"

    def test_notifications_since_filter(self, temp_factory_env):
        """railclaw-pipeline notifications --since filters correctly."""
        notifications_path = temp_factory_env["notifications_path"]
        notifications_path.parent.mkdir(parents=True, exist_ok=True)

        ts1 = "2024-01-01T00:00:00Z"
        ts2 = "2024-01-01T00:01:00Z"
        ts3 = "2024-01-01T00:02:00Z"
        with open(notifications_path, "a") as f:
            for ts in [ts1, ts2, ts3]:
                n = {"ts": ts, "type": "stage_end", "issue": 1, "stage": "stage1_blueprint"}
                f.write(json.dumps(n) + "\n")

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "railclaw_pipeline.cli",
                "notifications",
                "--since",
                "2024-01-01T00:01:00Z",
            ],
            capture_output=True,
        )
        data = json.loads(result.stdout.decode().strip())
        assert data["count"] == 2
        for n in data["notifications"]:
            assert n["ts"] >= "2024-01-01T00:01:00Z"

    def test_notifications_since_with_z_suffix(self, temp_factory_env):
        """railclaw-pipeline notifications --since handles ISO8601 Z suffix."""
        notifications_path = temp_factory_env["notifications_path"]
        notifications_path.parent.mkdir(parents=True, exist_ok=True)

        with open(notifications_path, "a") as f:
            for ts in ["2024-01-01T00:00:00Z", "2024-01-01T00:01:00Z"]:
                n = {"ts": ts, "type": "stage_end", "issue": 1, "stage": "stage1_blueprint"}
                f.write(json.dumps(n) + "\n")

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "railclaw_pipeline.cli",
                "notifications",
                "--since",
                "2024-01-01T00:00:30Z",
            ],
            capture_output=True,
        )
        data = json.loads(result.stdout.decode().strip())
        assert data["count"] == 1

    def test_notifications_limit(self, temp_factory_env):
        """railclaw-pipeline notifications --limit caps results."""
        notifications_path = temp_factory_env["notifications_path"]
        notifications_path.parent.mkdir(parents=True, exist_ok=True)

        with open(notifications_path, "a") as f:
            for i in range(10):
                n = {
                    "ts": f"2024-01-01T00:{i:02d}:00Z",
                    "type": "stage_end",
                    "issue": i,
                    "stage": "stage1_blueprint",
                }
                f.write(json.dumps(n) + "\n")

        result = subprocess.run(
            [sys.executable, "-m", "railclaw_pipeline.cli", "notifications", "--limit", "3"],
            capture_output=True,
        )
        data = json.loads(result.stdout.decode().strip())
        assert data["count"] == 3

    def test_notifications_malformed_lines_are_skipped(self, temp_factory_env):
        """railclaw-pipeline notifications skips malformed lines gracefully."""
        notifications_path = temp_factory_env["notifications_path"]
        notifications_path.parent.mkdir(parents=True, exist_ok=True)

        notifications_path.write_text(
            '{"ts": "2024-01-01T00:00:00Z", "type": "stage_end", "issue": 1, "stage": "stage1"}\n'
            "NOT VALID JSON\n"
            '{"ts": "2024-01-01T00:01:00Z", "type": "stage_end", "issue": 2, "stage": "stage2"}\n'
        )

        result = subprocess.run(
            [sys.executable, "-m", "railclaw_pipeline.cli", "notifications"],
            capture_output=True,
        )
        data = json.loads(result.stdout.decode().strip())
        assert data["count"] == 2


class TestNotificationRotation:
    def test_notifications_rotation_creates_archive(self, temp_factory_env, monkeypatch):
        """notifications.jsonl is rotated to .jsonl.1 when it exceeds MAX_NOTIFICATION_FILE_SIZE."""
        import railclaw_pipeline.events.notifications as notif_mod

        monkeypatch.setattr(notif_mod, "MAX_NOTIFICATION_FILE_SIZE", 512)

        notifications_path = temp_factory_env["notifications_path"]
        notifications_path.parent.mkdir(parents=True, exist_ok=True)

        for i in range(100):
            notif_mod.write_notification(
                notif_mod.NotificationPayload(
                    ts=f"2024-01-01T00:{i % 60:02d}:00Z",
                    type="stage_end",
                    issue=1,
                    stage="stage1",
                    verdict="pass",
                    findings_count=0,
                )
            )

        archive = notifications_path.with_suffix(".jsonl.1")
        assert archive.exists(), (
            "Rotation archive should be created when file exceeds MAX_NOTIFICATION_FILE_SIZE"
        )

    def test_notifications_rotation_max_3_archives(self, temp_factory_env, monkeypatch):
        """Rotation keeps at most MAX_ROTATED_FILES (3) archives."""
        import railclaw_pipeline.events.notifications as notif_mod

        monkeypatch.setattr(notif_mod, "MAX_NOTIFICATION_FILE_SIZE", 256)

        notifications_path = temp_factory_env["notifications_path"]
        notifications_path.parent.mkdir(parents=True, exist_ok=True)

        small_line = (
            json.dumps(
                {
                    "ts": "2024-01-01T00:00:00Z",
                    "type": "stage_end",
                    "issue": 1,
                    "stage": "stage1",
                }
            )
            + "\n"
        )

        for _i in range(20):
            with open(notifications_path, "ab") as f:
                f.write(small_line.encode() * 4)

        archives = sorted(notifications_path.parent.glob("notifications.jsonl.*"))
        assert len(archives) <= 3, (
            f"Should have at most 3 archives, got {len(archives)}: {archives}"
        )
