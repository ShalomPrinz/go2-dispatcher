"""The vision service's supervisor: start, ready wait, restart, stop (services/vision/docs/vision.md,
docs/running.md)."""

from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from services.vision import protocol

from .supervisor import ServiceSpec, ServiceSupervisor

LOG_FILE_NAME = "vision_service.log"
WAIT_POLL_S = 0.05  # ready-wait poll period, so an operator stop ends the wait (docs/safety.md)
SPEC = ServiceSpec(
    name="vision service",
    module=protocol.SERVICE_MODULE,
    ready_line=protocol.READY_LINE,
    startup_timeout_s=protocol.STARTUP_TIMEOUT_S,
    stop_grace_s=protocol.STOP_GRACE_S,
)


def log_path(log_dir: Path) -> Path:
    return Path(log_dir) / LOG_FILE_NAME


class VisionSupervisor(ServiceSupervisor):
    """One ``python -m services.vision.service`` process at a time, owned by the dispatcher.

    ``start`` does not wait; ``ensure_ready`` (before each vision step) restarts a dead service and waits for
    its ready line. A failed start is never raised: the step then reports ``vision_unavailable``."""

    def __init__(self, cwd: Path, log_file: Path, env: Callable[[], dict[str, str]]):
        super().__init__(SPEC, cwd, log_file, env)
        self._ready = threading.Event()
        self._started_at = 0.0

    # --- process ---------------------------------------------------------------------------

    def _spawn_locked(self) -> None:
        self._ready = threading.Event()
        self._started_at = time.monotonic()
        try:
            proc = self._spawn()
        except OSError as e:
            self._proc = None
            self._note(f"vision service failed to start: {type(e).__name__}: {e}")
            return
        self._proc = proc
        threading.Thread(target=self._read_ready, args=(proc, self._ready), daemon=True).start()

    def _read_ready(self, proc: subprocess.Popen, ready: threading.Event) -> None:
        """Set ``ready`` on the ready line; drain stdout until EOF (the service writes nothing else)."""
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                if line.strip() == self.spec.ready_line:
                    ready.set()
        except (OSError, ValueError):
            pass

    # --- lifecycle -------------------------------------------------------------------------

    def start(self) -> None:
        """Start the service without waiting for it to become ready."""
        with self._lock:
            if not self._closed and self._proc is None:
                self._spawn_locked()

    def ensure_ready(self, stop_event: threading.Event, limit_s: float) -> float:
        """Start the service if it is not running, then wait for ready up to ``startup_timeout_s`` from its start,
        at most ``limit_s``, ending early on ``stop_event``. Returns the seconds waited; never raises."""
        t0 = time.monotonic()
        with self._lock:
            if self._closed:
                return 0.0
            proc = self._proc
            if proc is None or proc.poll() is not None:
                if proc is not None:
                    self._note(f"vision service exited with code {proc.returncode}; restarting")
                    self._terminate(proc)
                self._spawn_locked()
            proc, ready = self._proc, self._ready
            started_at = self._started_at
        if proc is None:
            return time.monotonic() - t0
        startup_deadline = started_at + self.spec.startup_timeout_s
        deadline = min(startup_deadline, t0 + max(limit_s, 0.0))
        while not ready.is_set() and not stop_event.is_set() and proc.poll() is None:
            if time.monotonic() >= deadline:
                break
            ready.wait(WAIT_POLL_S)
        if not ready.is_set():
            self._not_ready(proc, startup_deadline)
        return time.monotonic() - t0

    def _not_ready(self, proc: subprocess.Popen, startup_deadline: float) -> None:
        """A service that exited before its ready line, or missed the startup timeout, is a failed start."""
        with self._lock:
            if self._proc is not proc:
                return
            if proc.poll() is not None:
                self._note(f"vision service exited with code {proc.returncode} before ready")
            elif time.monotonic() >= startup_deadline:
                self._note(f"vision service not ready after {self.spec.startup_timeout_s:g}s; stopping it")
                self._terminate(proc)
