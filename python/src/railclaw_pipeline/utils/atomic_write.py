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
    fd: int | None = None
    tmp_path: str | None = None
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent),
            suffix=".tmp",
            prefix="atomic_",
        )
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(content)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        fd = None
        os.replace(tmp_path, str(path))
        tmp_path = None
        return True
    except OSError:
        return False
    finally:
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
