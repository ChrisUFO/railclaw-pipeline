"""Shared JSONL file rotation with atomic semantics."""

import contextlib
import os
import shutil
import tempfile
from pathlib import Path


def rotate_jsonl(
    path: Path,
    max_size: int,
    max_archives: int,
) -> None:
    """Rotate a JSONL file when it exceeds *max_size*.

    Keeps up to *max_archives* rotated copies (``.jsonl.1``, ``.jsonl.2``, ...).
    Rotation is atomic via tempfile + fsync + os.replace.
    Uses shutil.copy2 to avoid loading the entire file into memory.
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
                src.rename(dst)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        suffix=".tmp",
        prefix=f"{path.stem}_",
    )
    try:
        os.close(fd)
        shutil.copy2(str(path), tmp_path)
        with open(tmp_path, "a") as tmp_file:
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, str(path.with_suffix(".jsonl.1")))
        path.unlink()
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise
