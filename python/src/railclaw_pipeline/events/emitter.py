"""JSON lines event emitter with buffered writes."""

import json
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class EventEmitter:
    """Buffers events in memory, flushes to disk periodically."""
    
    def __init__(self, events_path: Path, flush_interval: float = 30.0):
        self.events_path = events_path
        self.flush_interval = flush_interval
        self._buffer: deque[str] = deque()
        self._lock = threading.Lock()
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
    
    def emit(self, event_type: str, **kwargs: Any) -> None:
        """Emit an event to the buffer."""
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            **kwargs
        }
        line = json.dumps(event, default=str)
        
        with self._lock:
            self._buffer.append(line)
    
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
    
    def close(self) -> None:
        """Flush and close."""
        self.flush_now()
