"""Shared atomic file write utility — tempfile + fsync + os.replace."""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path


def atomic_write(path: Path, content: str) -> bool:
    """Write content to path atomically using tempfile + fsync + os.replace.

    Returns True if write succeeded, False on any error.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent),
            suffix=".tmp",
            prefix="atomic_",
        )
        try:
            with os.fdopen(fd, "w") as tmp_file:
                tmp_file.write(content)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
            os.replace(tmp_path, str(path))
            return True
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise
    except OSError:
        return False
