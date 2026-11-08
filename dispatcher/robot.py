"""The robot service's lifecycle, the dispatcher's state read through it, and the per-task state stream
(skills/docs/skills.md, docs/running.md, dispatcher/docs/run-log.md)."""

from __future__ import annotations

import dataclasses
import json
import selectors
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import IO, Any

from skills import robot_service
from skills.env import robot_lock_path
from skills.robot_ipc import LinkEvent, RobotClient, ServiceUnavailable

from .models import RobotServiceError, RobotState, StateReadError

LOG_FILE_NAME = "robot_service.log"
STREAM_PERIOD_S = 0.1  # state_stream.jsonl at 10 Hz (tunable, dispatcher/docs/run-log.md)
STREAM_POLL_S = 0.2  # the stream thread's wait per event, so close() ends it within this
STREAM_JOIN_S = 1.0  # close() waits at most this for the stream thread; it never blocks a task longer


def log_path(log_dir: Path) -> Path:
    return Path(log_dir) / LOG_FILE_NAME


class RobotService:
    """One ``python -m skills.robot_service`` process, owned by the dispatcher for its lifetime.

    ``start`` waits for the ready line and raises ``RobotServiceError`` on a failed start; ``read_state`` starts
    a dead service again once before it reads. Stderr and lifecycle notes go to ``log_file``, emptied at the
    first start of the process."""

    def __init__(self, cwd: Path, log_file: Path, socket_path: str, env: Callable[[], dict[str, str]]):
        self._cwd = Path(cwd)
        self._log_file = Path(log_file)
        self.socket_path = socket_path
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

    def _fail(self, text: str) -> RobotServiceError:
        self._note(text)
        return RobotServiceError(f"Robot service {text} (see {self._log_file}).")

    def _spawn_locked(self) -> None:
        """Start the service and wait for its ready line; raises RobotServiceError."""
        try:
            with self._open_log() as log:
                proc = subprocess.Popen(
                    [sys.executable, "-m", robot_service.__name__],
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
            raise self._fail(f"failed to start: {type(e).__name__}: {e}") from None
        self._note(f"robot service started (pid {proc.pid})")
        if self._wait_ready(proc):
            self._proc = proc
            return
        if proc.poll() is None:
            self._terminate(proc)
            raise self._fail(f"not ready after {robot_service.STARTUP_TIMEOUT_S:g}s; stopped it")
        self._close_stdout(proc)
        if proc.returncode == robot_service.EXIT_LOCKED:
            lock = robot_lock_path(self.socket_path)
            raise self._fail(f"not started: another robot service holds {lock}; stop it, or wait a few seconds")
        raise self._fail(f"exited with code {proc.returncode} before ready")

    @staticmethod
    def _wait_ready(proc: subprocess.Popen) -> bool:
        """Read stdout until the ready line, EOF or ``STARTUP_TIMEOUT_S``; the service writes nothing else."""
        assert proc.stdout is not None
        deadline = time.monotonic() + robot_service.STARTUP_TIMEOUT_S
        with selectors.DefaultSelector() as sel:
            sel.register(proc.stdout, selectors.EVENT_READ)
            while (left := deadline - time.monotonic()) > 0:
                if not sel.select(left):
                    return False
                line = proc.stdout.readline()
                if not line:
                    proc.wait()
                    return False
                if line.strip() == robot_service.READY_LINE:
                    return True
        return False

    @staticmethod
    def _close_stdout(proc: subprocess.Popen) -> None:
        if proc.stdout is not None:
            proc.stdout.close()

    def _terminate(self, proc: subprocess.Popen) -> None:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(robot_service.STOP_GRACE_S)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                self._note(f"robot service killed after {robot_service.STOP_GRACE_S:g}s grace")
        self._close_stdout(proc)

    # --- lifecycle -------------------------------------------------------------------------

    @property
    def pid(self) -> int | None:
        """The running service's pid, or None."""
        with self._lock:
            proc = self._proc
        return proc.pid if proc is not None and proc.poll() is None else None

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

    def read_state(self) -> RobotState:
        """The latest state through the service, starting a dead service once first. Raises StateReadError if
        the service cannot be started or reached, has had no message, or reports the link down."""
        try:
            self._ensure_running()
            with RobotClient.connect(self.socket_path) as client:
                reply = client.state()
        except (RobotServiceError, ServiceUnavailable) as e:
            raise StateReadError(f"{type(e).__name__}: {e}") from None
        if reply.state is None:
            raise StateReadError("the robot service has had no state message")
        if reply.link == "down":
            raise StateReadError(f"link down; the latest state message is {reply.state.age_ms} ms old")
        return reply.state

    def kill(self) -> None:
        """SIGKILL a dead or hung service (docs/safety.md); the next ``start`` or ``read_state`` starts it again."""
        with self._lock:
            proc = self._proc
            if proc is not None and proc.poll() is None:
                proc.kill()
                proc.wait()
                self._note("robot service lost; killed it")

    def stop(self) -> None:
        """SIGTERM, SIGKILL after ``STOP_GRACE_S``; idempotent, and the service is never started again."""
        with self._lock:
            self._closed = True
            proc, self._proc = self._proc, None
            if proc is not None:
                self._terminate(proc)
                self._note(f"robot service stopped (exit code {proc.returncode})")


class StateStream:
    """Writes the service's state stream for one task to ``path`` on a daemon thread, until ``close``.

    Every failure ends the stream with a ``stream_error`` line and nothing else: the stream never fails or
    blocks a task. ``t_mono_ms`` is relative to ``t_start`` (time.monotonic(), the task log's clock)."""

    def __init__(self, socket_path: str, path: Path, t_start: float, period_s: float = STREAM_PERIOD_S):
        self._socket_path = socket_path
        self._path = Path(path)
        self._t_start = t_start
        self._period_s = period_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="state-stream", daemon=True)
        try:
            self._thread.start()
        except RuntimeError:
            pass  # no thread, no stream; the task runs without it

    def close(self) -> None:
        self._stop.set()
        if self._thread.ident is not None:
            self._thread.join(STREAM_JOIN_S)

    def _ms(self, mono: float | None) -> float | None:
        return None if mono is None else (mono - self._t_start) * 1000.0

    def _run(self) -> None:
        try:
            f = self._path.open("a", encoding="utf-8")
        except OSError:
            return  # no file, nowhere to report; the task log is unaffected
        with f:

            def write(type: str, **fields: Any) -> None:
                f.write(json.dumps({"type": type, **fields}, ensure_ascii=False, default=str) + "\n")
                f.flush()

            try:
                self._stream(write)
            except Exception as e:  # noqa: BLE001 - the stream never fails a task (dispatcher/docs/run-log.md)
                try:
                    write("stream_error", t_mono_ms=self._ms(time.monotonic()), detail=f"{type(e).__name__}: {e}")
                except (OSError, ValueError):
                    pass

    def _stream(self, write: Callable[..., None]) -> None:
        with RobotClient.connect(self._socket_path) as client:
            link = client.subscribe(self._period_s)
            write(
                "stream_start",
                t_mono_ms=self._ms(time.monotonic()),
                period_ms=self._period_s * 1000.0,
                link=link,
            )
            while not self._stop.is_set():
                event = client.next_event(STREAM_POLL_S)
                if event is None:
                    continue
                if isinstance(event, LinkEvent):
                    write("link", t_mono_ms=self._ms(event.rx_mono), link=event.link)
                elif event.state is not None:
                    state = dataclasses.asdict(event.state)
                    write("sample", t_mono_ms=self._ms(event.rx_mono), link=event.link, state=state)
