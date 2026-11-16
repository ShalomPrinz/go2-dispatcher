"""The lifecycle shared by the robot and vision service supervisors: log file, spawn, terminate, stop, kill
(docs/running.md)."""

from __future__ import annotations

import subprocess
import sys
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import IO


@dataclass(frozen=True)
class ServiceSpec:
    """One service's lifecycle values, built by its supervisor from the service's ``protocol`` module."""

    name: str  # in the log notes, e.g. "robot service"
    module: str
    ready_line: str
    startup_timeout_s: float
    stop_grace_s: float


class ServiceSupervisor(ABC):
    """One ``python -m {spec.module}`` process at a time, owned by the dispatcher.

    Its stderr and the lifecycle notes go to ``log_file``, emptied at the first start of the process. A
    subclass defines ``start`` and how it waits for the ready line; ``stop`` is final."""

    def __init__(self, spec: ServiceSpec, cwd: Path, log_file: Path, env: Callable[[], dict[str, str]]):
        self.spec = spec
        self._cwd = Path(cwd)
        self._log_file = Path(log_file)
        self._env = env
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
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

    def _spawn(self) -> subprocess.Popen:
        """Start the process with stdout piped and stderr to the log; raises OSError. Does not wait."""
        with self._open_log() as log:
            proc = subprocess.Popen(
                [sys.executable, "-m", self.spec.module],
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
        self._note(f"{self.spec.name} started (pid {proc.pid})")
        return proc

    @staticmethod
    def _close_stdout(proc: subprocess.Popen) -> None:
        if proc.stdout is not None:
            proc.stdout.close()

    def _terminate(self, proc: subprocess.Popen) -> None:
        """SIGTERM, SIGKILL after ``stop_grace_s``; closes its stdout."""
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(self.spec.stop_grace_s)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                self._note(f"{self.spec.name} killed after {self.spec.stop_grace_s:g}s grace")
        self._close_stdout(proc)

    # --- lifecycle -------------------------------------------------------------------------

    @property
    def pid(self) -> int | None:
        """The running service's pid, or None."""
        with self._lock:
            proc = self._proc
        return proc.pid if proc is not None and proc.poll() is None else None

    @abstractmethod
    def start(self) -> None: ...

    def kill(self) -> None:
        """SIGKILL a dead or hung service (docs/safety.md); the next start starts it again."""
        with self._lock:
            proc = self._proc
            if proc is not None and proc.poll() is None:
                proc.kill()
                proc.wait()
                self._note(f"{self.spec.name} lost; killed it")

    def stop(self) -> None:
        """SIGTERM, SIGKILL after ``stop_grace_s``; idempotent, and the service is never started again."""
        with self._lock:
            self._closed = True
            proc, self._proc = self._proc, None
            if proc is not None:
                self._terminate(proc)
                self._note(f"{self.spec.name} stopped (exit code {proc.returncode})")
