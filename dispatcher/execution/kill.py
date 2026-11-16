"""The kill switch: the current skill process, and why and when it was killed, under one lock (docs/safety.md)."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Protocol

from ..models import StopCause


class Killable(Protocol):
    def poll(self) -> int | None: ...

    def kill(self) -> None: ...


@dataclass(frozen=True)
class Kill:
    cause: StopCause
    at_mono: float  # time.monotonic() at the SIGKILL


class KillSwitch:
    """Holds the one current process. A kill checks for exit and signals under the lock, so a reused PID is never
    signalled; the first kill cause wins (docs/safety.md)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current: Killable | None = None
        self._kill: Kill | None = None

    def register(self, proc: Killable) -> None:
        with self._lock:
            self._current, self._kill = proc, None

    def release(self) -> None:
        with self._lock:
            self._current, self._kill = None, None

    def poll(self, proc: Killable) -> tuple[int | None, Kill | None]:
        """``proc``'s exit code and the current kill, read under one lock."""
        with self._lock:
            return proc.poll(), self._kill

    def kill(self, proc: Killable, cause: StopCause) -> bool:
        """Kill ``proc`` if it is still current, running and not yet killed; returns whether it was killed."""
        with self._lock:
            if self._current is not proc or self._kill is not None:
                return False
            if proc.poll() is not None:
                return False
            self._kill = Kill(StopCause(cause), time.monotonic())
            proc.kill()
            return True

    def kill_current(self, cause: StopCause) -> bool:
        """Kill the current process, if any; returns whether one was killed."""
        with self._lock:
            proc = self._current
        if proc is None:
            return False
        return self.kill(proc, cause)
