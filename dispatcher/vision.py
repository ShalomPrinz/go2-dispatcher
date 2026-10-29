"""The vision service's lifecycle: start, ready wait, restart and stop (skills/docs/skills.md, docs/running.md)."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import IO

from skills import vision_service

LOG_FILE_NAME = "vision_service.log"
WAIT_POLL_S = 0.05  # ready-wait poll period, so an operator stop ends the wait (docs/safety.md)


def log_path(log_dir: Path) -> Path:
    return Path(log_dir) / LOG_FILE_NAME


class VisionService:
    """One ``python -m skills.vision_service`` process at a time, owned by the dispatcher.

    ``start`` does not wait; ``ensure_ready`` (before each vision step) restarts a dead service and waits for
    its ready line. A failed start is never raised: the step then reports ``vision_unavailable``. Its stderr
    and the lifecycle notes go to ``log_file``, truncated at the first start of the process."""

    def __init__(self, cwd: Path, log_file: Path, env: Callable[[], dict[str, str]]):
        self._cwd = Path(cwd)
        self._log_file = Path(log_file)
        self._env = env
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._ready = threading.Event()
        self._started_at = 0.0
        self._log_opened = False
        self._closed = False

    # --- log -------------------------------------------------------------------------------

    def _open_log(self) -> IO[str]:
        self._log_file.parent.mkdir(parents=True, exist_ok=True)
        if not self._log_opened:
            self._log_file.write_text("", encoding="utf-8")
            self._log_opened = True
        return open(self._log_file, "a", encoding="utf-8")  # O_APPEND: the service and these notes share it

    def _note(self, text: str) -> None:
        try:
            with self._open_log() as f:
                f.write(f"{datetime.now().isoformat(timespec='seconds')} dispatcher: {text}\n")
        except OSError:
            pass  # the log is for diagnosis only

    # --- process ---------------------------------------------------------------------------

    def _spawn_locked(self) -> None:
        self._ready = threading.Event()
        self._started_at = time.monotonic()
        try:
            with self._open_log() as log:
                proc = subprocess.Popen(
                    [sys.executable, "-m", vision_service.__name__],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=log,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    cwd=self._cwd,
                    env=self._env(),
                    start_new_session=True,
                )
        except OSError as e:
            self._proc = None
            self._note(f"vision service failed to start: {type(e).__name__}: {e}")
            return
        self._proc = proc
        self._note(f"vision service started (pid {proc.pid})")
        threading.Thread(target=self._read_ready, args=(proc, self._ready), daemon=True).start()

    @staticmethod
    def _read_ready(proc: subprocess.Popen, ready: threading.Event) -> None:
        """Set ``ready`` on the ready line; drain stdout until EOF (the service writes nothing else)."""
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                if line.strip() == vision_service.READY_LINE:
                    ready.set()
        except (OSError, ValueError):
            pass

    def _terminate_locked(self, proc: subprocess.Popen) -> None:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(vision_service.STOP_GRACE_S)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                self._note(f"vision service killed after {vision_service.STOP_GRACE_S:g}s grace")
        if proc.stdout is not None:
            proc.stdout.close()

    # --- lifecycle -------------------------------------------------------------------------

    @property
    def pid(self) -> int | None:
        """The running service's pid, or None."""
        with self._lock:
            proc = self._proc
        return proc.pid if proc is not None and proc.poll() is None else None

    def start(self) -> None:
        """Start the service without waiting for it to become ready."""
        with self._lock:
            if not self._closed and self._proc is None:
                self._spawn_locked()

    def ensure_ready(self, stop_event: threading.Event, limit_s: float) -> float:
        """Start the service if it is not running, then wait for ready up to ``STARTUP_TIMEOUT_S`` from its start,
        at most ``limit_s``, ending early on ``stop_event``. Returns the seconds waited; never raises."""
        t0 = time.monotonic()
        with self._lock:
            if self._closed:
                return 0.0
            proc = self._proc
            if proc is None or proc.poll() is not None:
                if proc is not None:
                    self._note(f"vision service exited with code {proc.returncode}; restarting")
                    self._terminate_locked(proc)
                self._spawn_locked()
            proc, ready = self._proc, self._ready
            started_at = self._started_at
        if proc is None:
            return time.monotonic() - t0
        startup_deadline = started_at + vision_service.STARTUP_TIMEOUT_S
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
                self._note(f"vision service not ready after {vision_service.STARTUP_TIMEOUT_S:g}s; stopping it")
                self._terminate_locked(proc)

    def stop(self) -> None:
        """SIGTERM, SIGKILL after ``STOP_GRACE_S``; idempotent, and the service is never started again."""
        with self._lock:
            self._closed = True
            proc, self._proc = self._proc, None
            if proc is not None:
                self._terminate_locked(proc)
                self._note(f"vision service stopped (exit code {proc.returncode})")
