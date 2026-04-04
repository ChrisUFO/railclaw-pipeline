"""JSON lines event emitter with buffered writes and rotation."""

import json
import threading
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAX_EVENT_FILE_SIZE = 10 * 1024 * 1024  # 10MB
MAX_ROTATED_FILES = 3


MAX_STDOUT_CHARS = 10_000


class EventEmitter:
    """Buffers events in memory, flushes to disk on demand."""

    def __init__(
        self,
        events_path: Path,
        run_dir: Path | None = None,
    ):
        self.events_path = events_path
        self.run_dir = run_dir
        self._buffer: deque[str] = deque()
        self._lock = threading.Lock()
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        if run_dir:
            run_dir.mkdir(parents=True, exist_ok=True)

    def emit_notification(
        self,
        notif_type: str,
        issue: int,
        stage: str,
        duration_s: float | None = None,
        verdict: str | None = None,
        findings_count: int | None = None,
        next_stage: str | None = None,
    ) -> None:
        """Emit a structured stage handoff notification."""
        from railclaw_pipeline.events.notifications import NotificationPayload, write_notification

        payload = NotificationPayload(
            ts=datetime.now(UTC).isoformat(),
            type=notif_type,
            issue=issue,
            stage=stage,
            duration_s=duration_s,
            verdict=verdict,
            findings_count=findings_count,
            next_stage=next_stage,
        )
        write_notification(payload)

    def emit(
        self,
        event_type: str,
        stdout: str | None = None,
        stderr: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Emit an event to the buffer."""
        event = {
            "ts": datetime.now(UTC).isoformat(),
            "type": event_type,
            **kwargs,
        }
        line = json.dumps(event, default=str)

        with self._lock:
            self._buffer.append(line)

        # Write agent stdout/stderr to per-run log
        if self.run_dir and (stdout or stderr):
            ts = event["ts"]
            agent = kwargs.get("agent", "unknown")
            log_file = self.run_dir / f"{event_type}_{agent}.log"
            with self._lock, open(log_file, "a") as f:
                if stdout:
                    truncated = len(stdout) > MAX_STDOUT_CHARS
                    f.write(f"--- STDOUT {ts} ---\n{stdout[:MAX_STDOUT_CHARS]}\n")
                    if truncated:
                        f.write(f"[truncated {MAX_STDOUT_CHARS} chars]\n")
                if stderr:
                    truncated = len(stderr) > MAX_STDOUT_CHARS
                    f.write(f"--- STDERR {ts} ---\n{stderr[:MAX_STDOUT_CHARS]}\n")
                    if truncated:
                        f.write(f"[truncated {MAX_STDOUT_CHARS} chars]\n")

    def flush_now(self) -> None:
        """Flush immediately - called on stage transitions and shutdown."""
        with self._lock:
            if not self._buffer:
                return
            lines = list(self._buffer)
            self._buffer.clear()

            with open(self.events_path, "a") as f:
                for line in lines:
                    f.write(line + "\n")
                f.flush()

            self._rotate_events()

    def _rotate_events(self) -> None:
        """Rotate events.jsonl at 10MB, keep 3 archives."""
        if not self.events_path.exists():
            return
        if self.events_path.stat().st_size < MAX_EVENT_FILE_SIZE:
            return
        # Shift existing archives
        for i in range(MAX_ROTATED_FILES, 0, -1):
            src = self.events_path.with_suffix(f".jsonl.{i}")
            if src.exists():
                if i == MAX_ROTATED_FILES:
                    src.unlink()  # Delete oldest
                else:
                    dst = self.events_path.with_suffix(f".jsonl.{i + 1}")
                    src.rename(dst)
        self.events_path.rename(self.events_path.with_suffix(".jsonl.1"))

    def close(self) -> None:
        """Flush and close."""
        self.flush_now()
