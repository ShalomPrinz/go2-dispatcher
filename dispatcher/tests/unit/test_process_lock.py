"""Process lock unit tests (tests/docs/testing.md)."""

from __future__ import annotations

import subprocess
import sys

from dispatcher import process_lock

# The child first takes a free lock (must succeed), then the parent's lock (must raise StartupError).
CHILD = """
import sys
from dispatcher.models import StartupError
from dispatcher.process_lock import acquire
acquire(sys.argv[1])
print("free lock acquired", flush=True)
try:
    acquire(sys.argv[2])
except StartupError as e:
    print(e)
"""


def test_lock_held_by_parent_blocks_child(tmp_path):
    log_dir = tmp_path / "runs"
    path = process_lock.acquire(log_dir)
    assert path == (log_dir / ".dispatcher.lock").resolve()
    assert path.exists()
    proc = subprocess.run(
        [sys.executable, "-c", CHILD, str(tmp_path / "other"), str(log_dir)], capture_output=True, text=True, timeout=20
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == ["free lock acquired", f"Another dispatcher is running (lock: {path})."]
    # re-acquire in the same process is a no-op
    assert process_lock.acquire(log_dir) == path
