"""Process lock unit tests (§19.2 process lock)."""

from __future__ import annotations

import subprocess
import sys

from go2_dispatcher import process_lock

CHILD = "import sys; from go2_dispatcher.process_lock import acquire; acquire(sys.argv[1])"


def test_second_acquire_in_child_fails_with_exit_2(tmp_path):
    log_dir = tmp_path / "runs"
    path = process_lock.acquire(log_dir)
    assert path == (log_dir / ".dispatcher.lock").resolve()
    assert path.exists()
    proc = subprocess.run([sys.executable, "-c", CHILD, str(log_dir)],
                          capture_output=True, text=True, timeout=20)
    assert proc.returncode == 2
    assert proc.stderr.strip() == f"Another dispatcher is running (lock: {path})."
    # re-acquire in the same process is a no-op
    assert process_lock.acquire(log_dir) == path


def test_acquire_free_lock_in_child_succeeds(tmp_path):
    proc = subprocess.run([sys.executable, "-c", CHILD, str(tmp_path / "other")],
                          capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, proc.stderr
