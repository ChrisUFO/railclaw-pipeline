"""Atomic state persistence with crash recovery."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from railclaw_pipeline.state.models import PipelineState


class StatePersistenceError(Exception):
    """Raised when state persistence fails."""
    pass


def save_state(state: PipelineState, state_path: Path) -> None:
    """Atomic write: write to temp file → flush → os.replace().
    
    os.replace() is atomic on POSIX (Linux/macOS), ensuring the target file
    is never in a partially-written state.
    """
    state_path.parent.mkdir(parents=True, exist_ok=True)
    data = state.model_dump_json(indent=2)
    
    fd, tmp_path = tempfile.mkstemp(
        dir=str(state_path.parent),
        suffix=".tmp",
        prefix="state_"
    )
    
    try:
        with os.fdopen(fd, "w") as tmp_file:
            tmp_file.write(data)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, str(state_path))
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def load_state(state_path: Path) -> PipelineState:
    """Load state.json. Raises FileNotFoundError if file doesn't exist."""
    if not state_path.exists():
        raise FileNotFoundError(f"State file not found: {state_path}")
    
    try:
        data = state_path.read_text()
        return PipelineState.model_validate_json(data)
    except (json.JSONDecodeError, ValueError, OSError) as e:
        raise StatePersistenceError(f"Failed to load state from {state_path}: {e}") from e


def delete_state(state_path: Path) -> None:
    """Delete state file if it exists."""
    if state_path.exists():
        state_path.unlink()
