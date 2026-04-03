"""Tests for EventEmitter — rotation, per-run logs."""

import json
from pathlib import Path

from railclaw_pipeline.events.emitter import EventEmitter, MAX_EVENT_FILE_SIZE


def test_emit_and_flush(tmp_path: Path):
    """Basic emit + flush writes to events.jsonl."""
    events_path = tmp_path / "events.jsonl"
    emitter = EventEmitter(events_path)
    emitter.emit("test_event", key="value")
    emitter.flush_now()

    lines = events_path.read_text().strip().split("\n")
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["type"] == "test_event"
    assert data["key"] == "value"


def test_rotate_at_10mb(tmp_path: Path):
    """Rotation triggers when file exceeds 10MB."""
    events_path = tmp_path / "events.jsonl"
    emitter = EventEmitter(events_path)

    # Write enough data to exceed 10MB
    big_body = "x" * 1024
    for _ in range(10_500):  # ~10.5MB
        emitter.emit("big_event", data=big_body)
    emitter.flush_now()

    assert events_path.with_suffix(".jsonl.1").exists()
    # Main file should have been rotated (may be empty or have remaining buffer)


def test_max_3_rotated(tmp_path: Path):
    """Only .1, .2, .3 archives kept — .4 is deleted."""
    events_path = tmp_path / "events.jsonl"
    emitter = EventEmitter(events_path)

    big_body = "x" * 1024

    # Force 4 rotations
    for rotation in range(4):
        # Pre-create archive files for rotation chain
        for i in range(1, 4):
            archive = events_path.with_suffix(f".jsonl.{i}")
            archive.write_text("old data")

        for _ in range(10_500):
            emitter.emit("big_event", data=big_body)
        emitter.flush_now()

    # After all rotations, only .1, .2, .3 should exist
    for i in range(1, 4):
        assert events_path.with_suffix(f".jsonl.{i}").exists()
    assert not events_path.with_suffix(".jsonl.4").exists()


def test_per_run_log_stdout_stderr(tmp_path: Path):
    """Stdout/stderr written to per-run log files."""
    run_dir = tmp_path / "runs" / "issue-42"
    emitter = EventEmitter(tmp_path / "events.jsonl", run_dir=run_dir)

    emitter.emit("agent_end", agent="wrench", stdout="build success", stderr="")

    log_files = list(run_dir.glob("*.log"))
    assert len(log_files) == 1
    content = log_files[0].read_text()
    assert "--- STDOUT" in content
    assert "build success" in content


def test_per_run_log_no_dir(tmp_path: Path):
    """No per-run logs when run_dir is not set."""
    events_path = tmp_path / "events.jsonl"
    emitter = EventEmitter(events_path)

    emitter.emit("agent_end", agent="wrench", stdout="output")
    emitter.flush_now()

    # No run dirs created
    assert not (tmp_path / "runs").exists()
    assert events_path.exists()
