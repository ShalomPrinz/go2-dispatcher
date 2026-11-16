"""The robot service's supervisor and the dispatcher's state read through it
(services/robot/docs/service.md, docs/running.md)."""

from __future__ import annotations

import selectors
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from contract.env import robot_lock_path
from services.robot import protocol
from services.robot.client import RobotClient, ServiceUnavailable

from ..models import RobotServiceError, RobotServiceLostError, RobotState, StateReadError
from .supervisor import ServiceSpec, ServiceSupervisor

LOG_FILE_NAME = "robot_service.log"
SPEC = ServiceSpec(
    name="robot service",
    module=protocol.SERVICE_MODULE,
    ready_line=protocol.READY_LINE,
    startup_timeout_s=protocol.STARTUP_TIMEOUT_S,
    stop_grace_s=protocol.STOP_GRACE_S,
)


def log_path(log_dir: Path) -> Path:
    return Path(log_dir) / LOG_FILE_NAME


class RobotSupervisor(ServiceSupervisor):
    """One ``python -m services.robot.service`` process, owned by the dispatcher for its lifetime.

    ``start`` waits for the ready line and raises ``RobotServiceError`` on a failed start; ``read_state`` starts
    a dead service again once before it reads."""

    def __init__(self, cwd: Path, log_file: Path, socket_path: str, env: Callable[[], dict[str, str]]):
        super().__init__(SPEC, cwd, log_file, env)
        self.socket_path = socket_path

    # --- process ---------------------------------------------------------------------------

    def _fail(self, text: str) -> RobotServiceError:
        self._note(text)
        return RobotServiceError(f"Robot service {text} (see {self._log_file}).")

    def _spawn_locked(self) -> None:
        """Start the service and wait for its ready line; raises RobotServiceError."""
        try:
            proc = self._spawn()
        except OSError as e:
            raise self._fail(f"failed to start: {type(e).__name__}: {e}") from None
        if self._wait_ready(proc):
            self._proc = proc
            return
        if proc.poll() is None:
            self._terminate(proc)
            raise self._fail(f"not ready after {self.spec.startup_timeout_s:g}s; stopped it")
        self._close_stdout(proc)
        if proc.returncode == protocol.EXIT_LOCKED:
            lock = robot_lock_path(self.socket_path)
            raise self._fail(f"not started: another robot service holds {lock}; stop it, or wait a few seconds")
        raise self._fail(f"exited with code {proc.returncode} before ready")

    def _wait_ready(self, proc: subprocess.Popen) -> bool:
        """Read stdout until the ready line, EOF or ``startup_timeout_s``; the service writes nothing else."""
        assert proc.stdout is not None
        deadline = time.monotonic() + self.spec.startup_timeout_s
        with selectors.DefaultSelector() as sel:
            sel.register(proc.stdout, selectors.EVENT_READ)
            while (left := deadline - time.monotonic()) > 0:
                if not sel.select(left):
                    return False
                line = proc.stdout.readline()
                if not line:
                    proc.wait()
                    return False
                if line.strip() == self.spec.ready_line:
                    return True
        return False

    # --- lifecycle -------------------------------------------------------------------------

    def start(self) -> None:
        """Start the service and wait until it is ready; raises RobotServiceError. A no-op while it runs."""
        self._ensure_running()

    def _ensure_running(self) -> None:
        with self._lock:
            if self._closed:
                raise RobotServiceError("Robot service stopped: the dispatcher is shutting down.")
            proc = self._proc
            if proc is not None and proc.poll() is None:
                return
            if proc is not None:
                self._note(f"robot service exited with code {proc.returncode}; restarting")
                self._close_stdout(proc)
                self._proc = None
            self._spawn_locked()

    def read_state(self, *, start: bool = True) -> RobotState:
        """The latest state through the service, starting a dead service once first unless ``start`` is false.
        Raises StateReadError if the service cannot be started or reached, has had no message, or reports the
        link down; RobotServiceLostError (a StateReadError) if ``start`` is false and it is dead or unreachable."""
        if not start and self.pid is None:
            raise RobotServiceLostError("the robot service is not running")
        try:
            if start:
                self._ensure_running()
            with RobotClient.connect(self.socket_path) as client:
                reply = client.state()
        except (RobotServiceError, ServiceUnavailable) as e:
            error = RobotServiceLostError if not start else StateReadError  # a dead service at a pre-step read
            raise error(f"{type(e).__name__}: {e}") from None
        if reply.state is None:
            raise StateReadError("the robot service has had no state message")
        if reply.link == "down":
            raise StateReadError(f"link down; the latest state message is {reply.state.age_ms} ms old")
        return reply.state
