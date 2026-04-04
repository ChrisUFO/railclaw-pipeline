"""Shared JSONL file rotation with atomic semantics."""

import os
from pathlib import Path


def rotate_jsonl(
    path: Path,
    max_size: int,
    max_archives: int,
) -> None:
    """Rotate a JSONL file when it exceeds *max_size*.

    Keeps up to *max_archives* rotated copies (``.jsonl.1``, ``.jsonl.2``, ...).
    Uses ``os.replace`` for instant, atomic rotation on the same filesystem.
    """
    if not path.exists():
        return
    if path.stat().st_size < max_size:
        return
    for i in range(max_archives, 0, -1):
        src = path.with_suffix(f".jsonl.{i}")
        if src.exists():
            if i == max_archives:
                src.unlink()
            else:
                dst = path.with_suffix(f".jsonl.{i + 1}")
                os.replace(str(src), str(dst))
    archive = path.with_suffix(".jsonl.1")
    try:
        os.replace(str(path), str(archive))
    except OSError:
        raise
