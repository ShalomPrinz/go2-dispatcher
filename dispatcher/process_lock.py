"""Machine-wide single-instance lock (docs/safety.md)."""

from __future__ import annotations

import fcntl
from pathlib import Path
from typing import IO

from .models import StartupError

LOCK_FILE_NAME = ".dispatcher.lock"

# Held for the life of the process; never closed explicitly.
_held: dict[Path, IO[str]] = {}


def lock_path(log_dir: Path) -> Path:
    return Path(log_dir) / LOCK_FILE_NAME


def acquire(log_dir: Path) -> Path:
    """Take an exclusive, non-blocking flock on ``{log_dir}/.dispatcher.lock``.

    If another process holds it, raise ``StartupError`` with
    ``Another dispatcher is running (lock: <path>).`` Re-acquiring in the same process is a no-op.
    Returns the lock file path."""
    path = lock_path(log_dir).resolve()
    if path in _held:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a+", encoding="utf-8")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        raise StartupError(f"Another dispatcher is running (lock: {path}).") from None
    _held[path] = f
    return path
