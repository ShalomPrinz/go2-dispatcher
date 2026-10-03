"""The skill process's own resources: the saved stdout fd that carries the one response line,
and the orphan watchdog on the parent dispatcher (skills/docs/skills.md). Standard library only."""

from __future__ import annotations

import os
import sys
import threading
import time

from skills.env import PARENT_PID

WATCHDOG_POLL_S = 0.2
ORPHAN_GRACE_S = 2.0
ORPHAN_EXIT_CODE = 137

_saved_stdout_fd: int | None = None
ORPHANED = False
_watchdog_started = False


# --- stdout capture -----------------------------------------------------------


def capture_stdout() -> None:
    """Call first in main(). Duplicate fd 1 to a saved fd, then dup2 fd 2 onto fd 1,
    so anything printed by the SDK, cyclonedds, ultralytics or C code goes to stderr.
    emit() writes only to the saved fd."""
    global _saved_stdout_fd
    if _saved_stdout_fd is not None:
        return
    try:
        sys.stdout.flush()
    except Exception:
        pass
    _saved_stdout_fd = os.dup(1)
    os.dup2(2, 1)


def _out_fd() -> int:
    return _saved_stdout_fd if _saved_stdout_fd is not None else 1


def write_raw_stdout(text: str) -> None:
    """Write text to the saved stdout fd: the response line, or the stub's `garbage` fault."""
    data = text.encode("utf-8")
    fd = _out_fd()
    while data:
        n = os.write(fd, data)
        data = data[n:]


def fsync_stdout() -> None:
    """fsync the saved stdout fd; pipes and terminals cannot be fsynced, so OSError is ignored."""
    try:
        os.fsync(_out_fd())
    except OSError:
        pass


# --- orphan watchdog ----------------------------------------------------------


def orphaned() -> bool:
    return ORPHANED


def _watchdog(parent_pid: int) -> None:
    global ORPHANED
    while os.getppid() == parent_pid:
        time.sleep(WATCHDOG_POLL_S)
    ORPHANED = True
    print(f"orphan watchdog: parent {parent_pid} is gone; exiting in {ORPHAN_GRACE_S:g}s", file=sys.stderr, flush=True)
    time.sleep(ORPHAN_GRACE_S)
    os._exit(ORPHAN_EXIT_CODE)


def start_orphan_watchdog() -> None:
    """Start a daemon thread that polls os.getppid() every 0.2 s. If it differs from
    int(os.environ["GO2_PARENT_PID"]), set the module flag ORPHANED; if the process is
    still alive 2.0 s later, os._exit(137). Motion loops check orphaned() each iteration
    and break (then StopMove). Does nothing if GO2_PARENT_PID is unset (manual runs)."""
    global _watchdog_started
    raw = os.environ.get(PARENT_PID, "").strip()
    if not raw or _watchdog_started:
        return
    try:
        parent_pid = int(raw)
    except ValueError:
        print(f"orphan watchdog: ignoring invalid {PARENT_PID}={raw!r}", file=sys.stderr)
        return
    _watchdog_started = True
    threading.Thread(target=_watchdog, args=(parent_pid,), name="orphan-watchdog", daemon=True).start()
