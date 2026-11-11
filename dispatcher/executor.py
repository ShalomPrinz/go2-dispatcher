"""Executor: one subprocess per skill call under a robot service lease, timeouts, kill, the stop path with its
fallback, robot service loss (docs/safety.md); owns the vision and robot services (docs/running.md)."""

from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel, TypeAdapter, ValidationError

from skills import detect_object as vision_client
from skills import read_state as read_state_utility
from skills import stop_move as stop_move_utility
from skills.env import child_env, robot_socket_path
from skills.policy import SkillPolicy
from skills.robot_ipc import MAX_TIMEOUT_S, Refused, RobotClient, ServiceUnavailable

from . import prompts
from .config import SECRET_ENV, Config
from .models import RobotServiceError, RobotState, SkillResponse, StateReadError, StopCause, StopMoveResult
from .registry import SkillDescriptor
from .robot import RobotService, StateStream
from .robot import log_path as robot_log_path
from .vision import VisionService
from .vision import log_path as vision_log_path

if TYPE_CHECKING:
    from .runlog import TaskObserver

POLL_INTERVAL_S = 0.05  # wait-loop poll period (docs/safety.md)
READER_JOIN_TIMEOUT_S = 2.0  # join timeout per reader thread (docs/safety.md)
STDERR_TAIL_CHARS = 2000  # stderr kept for the log (skills/docs/skills.md)
STOP_WAIT_S = 0.5  # wait for stop_sent or pong before the service counts as lost (tunable, docs/safety.md)
LIVENESS_PERIOD_S = 0.5  # ping period on the control connection while a step runs (tunable, docs/safety.md)

_SERVICE_ERRORS = (RobotServiceError, ServiceUnavailable, Refused)  # the control connection cannot be used

_RESPONSE_ADAPTER = TypeAdapter(SkillResponse)  # built once; validates the stdlib dataclass


ExecOutcome = Literal["ok", "error", "timeout", "malformed", "interrupted"]


class ExecResult(BaseModel):
    outcome: ExecOutcome
    interrupt_cause: StopCause | None = None  # set iff outcome is interrupted
    response: SkillResponse | None = None
    exit_code: int | None = None
    pid: int | None = None
    duration_ms: float
    stderr_tail: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    stop_move: StopMoveResult | None = None
    service_lost: bool = False  # the robot service was lost during the step and killed (docs/safety.md)


class SkillExecutor(Protocol):
    """What the dispatcher and transports need from an executor; ``Executor`` or a test fake."""

    def run(
        self,
        skill: SkillDescriptor,
        params: dict,
        *,
        fault: str | None,
        timeout_s: float,
        remaining_task_s: float,
        stop_event: threading.Event,
    ) -> ExecResult: ...

    def kill_current(self, cause: StopCause) -> bool: ...

    def stop_move(self, reason: StopCause) -> StopMoveResult:
        """The stop path: the service's ``stop``, else the fallback ``stop_move`` process; never raises."""
        ...

    def fresh_state(self, *, start: bool = True) -> RobotState:
        """The latest state through the robot service, starting a dead service first unless ``start`` is false
        (a pre-step read never starts it); raises ``models.StateReadError``, ``models.RobotServiceLostError`` when
        ``start`` is false and the service is dead or unreachable."""
        ...

    def state_stream(self, path: Path, t_start: float, observer: TaskObserver | None = None) -> StreamHandle:
        """Start writing the robot service's state stream to ``path``, and handing it to ``observer``, until the
        handle is closed."""
        ...

    def close(self) -> None: ...


class StreamHandle(Protocol):
    def close(self) -> None: ...


def needs_vision(skill: SkillDescriptor) -> bool:
    """Whether ``skill`` is a client of the vision service (skills/docs/skills.md)."""
    return skill.entrypoint == vision_client.__name__


def _stderr_tail(text: str | None) -> str | None:
    """The last STDERR_TAIL_CHARS characters of ``text``, or None when empty."""
    return (text or "")[-STDERR_TAIL_CHARS:] or None


class _Readers:
    """Two daemon threads draining stdout (kept whole) and stderr (tail only)."""

    def __init__(self, proc: subprocess.Popen):
        self._stdout: list[str] = []
        self._stderr = ""
        self._threads = [
            threading.Thread(target=self._read_stdout, args=(proc.stdout,), daemon=True),
            threading.Thread(target=self._read_stderr, args=(proc.stderr,), daemon=True),
        ]
        for t in self._threads:
            t.start()

    def _read_stdout(self, stream) -> None:
        try:
            for chunk in iter(lambda: stream.read(4096), ""):
                self._stdout.append(chunk)
        except (OSError, ValueError):
            pass

    def _read_stderr(self, stream) -> None:
        try:
            for chunk in iter(lambda: stream.read(4096), ""):
                self._stderr = _stderr_tail(self._stderr + chunk) or ""
        except (OSError, ValueError):
            pass

    def join(self) -> None:
        for t in self._threads:
            t.join(READER_JOIN_TIMEOUT_S)

    @property
    def stdout(self) -> str:
        return "".join(self._stdout)

    @property
    def stderr_tail(self) -> str | None:
        return _stderr_tail(self._stderr)


def _parse_response(stdout: str, skill_name: str) -> SkillResponse | None:
    """Last non-empty stdout line as a SkillResponse for ``skill_name``, else None."""
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    if not lines:
        return None
    try:
        resp = _RESPONSE_ADAPTER.validate_json(lines[-1])
    except ValidationError:
        return None
    return resp if resp.skill == skill_name else None


def _killpg(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _ms(t0: float) -> float:
    return (time.monotonic() - t0) * 1000.0


def _error_text(e: BaseException) -> str:
    return f"{type(e).__name__}: {e}"


class Executor:
    def __init__(self, cfg: Config, base_dir: Path):
        self.cfg = cfg
        self.base_dir = Path(base_dir)
        self._lock = threading.Lock()
        self._current: subprocess.Popen | None = None
        self._kill_cause: StopCause | None = None
        self._kill_mono: float | None = None  # when the current process was killed
        self._control_lock = threading.Lock()  # one user of the control connection at a time
        self._client: RobotClient | None = None  # the control connection (skills/docs/skills.md)
        self.vision = VisionService(self.base_dir, vision_log_path(cfg.log.dir), lambda: self._env(None))
        self.robot = RobotService(
            self.base_dir, robot_log_path(cfg.log.dir), robot_socket_path(cfg.log.dir), lambda: self._env(None)
        )

    # --- environment and process start (skills/docs/skills.md) ----------------------------------

    def _env(self, fault: str | None, lease_token: str | None = None) -> dict[str, str]:
        return child_env(
            {k: v for k, v in os.environ.items() if k not in SECRET_ENV},
            backend=self.cfg.robot.backend,
            network_interface=self.cfg.robot.network_interface,
            yolo_weights=str(self.cfg.robot.yolo_weights),
            stub_state_file=str(self.cfg.stub.state_file),
            stub_time_scale=self.cfg.stub.time_scale,
            stub_objects=self.cfg.stub.objects,
            parent_pid=os.getpid(),
            log_dir=self.cfg.log.dir,
            fault=fault,
            lease_token=lease_token,
        )

    def _popen(self, module: str, params: dict, env: dict[str, str]) -> subprocess.Popen:
        argv = [sys.executable, "-m", module, json.dumps(params)]
        return subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=self.base_dir,
            env=env,
            start_new_session=True,
        )

    # --- kill (docs/safety.md) --------------------------------------------------------------

    def _kill_locked(self, proc: subprocess.Popen, cause: StopCause) -> bool:
        with self._lock:
            if self._current is not proc or self._kill_cause is not None:
                return False
            if proc.poll() is not None:
                return False
            self._kill_cause = StopCause(cause)
            self._kill_mono = time.monotonic()
            _killpg(proc.pid)
            return True

    def _killed_locked(self) -> tuple[StopCause | None, float | None]:
        """Why and when the current process was killed, if it was (caller holds the lock)."""
        return self._kill_cause, self._kill_mono

    def _release_locked(self) -> tuple[StopCause | None, float | None]:
        """Clear the current process (caller holds the lock); returns why and when it was killed, if it was."""
        released = self._killed_locked()
        self._current, self._kill_cause, self._kill_mono = None, None, None
        return released

    def kill_current(self, cause: StopCause) -> bool:
        """Kill the running skill process, if any; returns whether one was killed."""
        with self._lock:
            proc = self._current
        if proc is None:
            return False
        return self._kill_locked(proc, cause)

    # --- run (skills/docs/skills.md, docs/safety.md) -----------------------------------------------------------

    def run(
        self,
        skill: SkillDescriptor,
        params: dict,
        *,
        fault: str | None,
        timeout_s: float,
        remaining_task_s: float,
        stop_event: threading.Event,
    ) -> ExecResult:
        if needs_vision(skill):  # the wait counts against the task, not the step
            remaining_task_s -= self.vision.ensure_ready(stop_event, remaining_task_s)
        t0 = time.monotonic()
        token = secrets.token_hex(16)
        try:
            with self._control_lock:
                self._control().arm(token, min(timeout_s, MAX_TIMEOUT_S))
        except _SERVICE_ERRORS as e:
            stop = self._fallback(StopCause.ROBOT_SERVICE_LOST, t0, None, service_error=_error_text(e), lose=True)
            return self._not_started(stop, duration_ms=_ms(t0))
        proc = self._popen(skill.entrypoint, params, self._env(fault, lease_token=token))
        readers = _Readers(proc)
        with self._lock:
            self._current = proc
            self._kill_cause = None
            self._kill_mono = None

        lost: str | None = None  # why the service counts as lost, once it does
        stop: StopMoveResult | None = None
        service_lost = False
        next_ping = t0 + LIVENESS_PERIOD_S
        while True:
            with self._lock:
                rc = proc.poll()
                cause, kill_mono = self._killed_locked()
            if cause is not None and stop is None:  # stop now, in parallel with the SIGKILL (docs/safety.md)
                stop, service_lost = self._stop_after_kill(cause, kill_mono, lost)
            if rc is not None:
                with self._lock:
                    self._release_locked()
                break
            now = time.monotonic()
            elapsed = now - t0
            if cause is not None:
                pass  # killed and stopped: wait for the exit
            elif now >= next_ping:
                next_ping = now + LIVENESS_PERIOD_S
                lost = self._ping()
                if lost is not None:  # SIGKILL the service and the skill, then the fallback stop above
                    self.robot.kill()
                    self._kill_locked(proc, StopCause.ROBOT_SERVICE_LOST)
            elif stop_event.is_set():
                self._kill_locked(proc, StopCause.OPERATOR)
            elif elapsed >= remaining_task_s:
                self._kill_locked(proc, StopCause.TASK_TIME_LIMIT)
            elif elapsed >= timeout_s:
                self._kill_locked(proc, StopCause.STEP_TIMEOUT)
            time.sleep(POLL_INTERVAL_S)

        if stop is None and lost is not None:  # lost after the skill had exited: nothing was killed
            stop = self._fallback(StopCause.ROBOT_SERVICE_LOST, time.monotonic(), None, service_error=lost, lose=True)
            service_lost = True
        elif stop is None:  # no stop sent by the executor: the disarm reports the service's own failed StopMove
            stop, service_lost = self._disarm()
        readers.join()
        duration_ms = _ms(t0)

        response = _parse_response(readers.stdout, skill.name)

        outcome: ExecOutcome
        interrupt_cause: StopCause | None = None
        if cause == StopCause.ROBOT_SERVICE_LOST:
            outcome, error_code = "error", StopCause.ROBOT_SERVICE_LOST.value
            error_message = prompts.KILL_ROBOT_SERVICE_LOST
        elif cause is not None:
            outcome, error_code, template = cause.kill
            interrupt_cause = cause if outcome == "interrupted" else None
            error_message = template.format(timeout_s=timeout_s)
        elif response is None:
            outcome, error_code = "malformed", "malformed"
            error_message = prompts.MALFORMED_RESPONSE.format(rc=rc)
        else:
            outcome = response.status
            error_code = response.error.code if response.error else None
            error_message = response.error.message if response.error else None
        return ExecResult(
            outcome=outcome,
            interrupt_cause=interrupt_cause,
            error_code=error_code,
            error_message=error_message,
            stop_move=stop,
            service_lost=service_lost,
            exit_code=rc,
            pid=proc.pid,
            duration_ms=duration_ms,
            stderr_tail=readers.stderr_tail,
            response=response,
        )

    @staticmethod
    def _not_started(stop: StopMoveResult, *, duration_ms: float) -> ExecResult:
        """A step that never started because the service could not be armed."""
        return ExecResult(
            outcome="error",
            error_code=StopCause.ROBOT_SERVICE_LOST.value,
            error_message=prompts.KILL_ROBOT_SERVICE_LOST,
            stop_move=stop,
            service_lost=True,
            duration_ms=duration_ms,
        )

    # --- utilities (docs/safety.md) ------------------------------------------------------------

    def _run_utility(self, utility: ModuleType) -> tuple[int | None, SkillResponse | None, str | None, float]:
        """Run a utility module (never registered as current) with its POLICY name and timeout.
        Returns (exit_code, response, stderr_tail, duration_ms)."""
        policy: SkillPolicy = utility.POLICY
        t0 = time.monotonic()
        proc = self._popen(utility.__name__, {}, self._env(None))
        try:
            out, err = proc.communicate(timeout=policy.timeout_s({}))
        except subprocess.TimeoutExpired:
            _killpg(proc.pid)
            out, err = proc.communicate()
        duration_ms = _ms(t0)
        response = _parse_response(out or "", policy.name)
        tail = _stderr_tail(err)
        return proc.returncode, response, tail, duration_ms

    # --- stop path (docs/safety.md) ----------------------------------------------------------

    def _stop_after_kill(
        self, cause: StopCause, kill_mono: float | None, lost: str | None
    ) -> tuple[StopMoveResult, bool]:
        """The stop for a killed skill: the fallback when the service is already lost, else the service's ``stop``."""
        if lost is not None:
            t_lost = kill_mono if kill_mono is not None else time.monotonic()
            return self._fallback(StopCause.ROBOT_SERVICE_LOST, t_lost, kill_mono, service_error=lost, lose=True), True
        return self._stop(cause, kill_mono)

    def stop_move(self, reason: StopCause) -> StopMoveResult:
        return self._stop(reason, None)[0]

    def _stop(self, reason: StopCause, kill_mono: float | None) -> tuple[StopMoveResult, bool]:
        """The service's ``stop``; the fallback when no ``stop_sent`` arrives in STOP_WAIT_S or StopMove failed.
        Returns the result and whether the service was lost (and killed)."""
        t0 = time.monotonic()
        try:
            with self._control_lock:
                sent = self._control().stop(STOP_WAIT_S)
        except _SERVICE_ERRORS as e:
            return self._fallback(reason, t0, kill_mono, service_error=_error_text(e), lose=True), True
        if sent.ret != 0:  # the service answered but its StopMove failed: the fallback has its own DDS
            error = f"StopMove returned {sent.ret}"
            smr = self._fallback(reason, t0, kill_mono, service_error=error, stop_call_ms=sent.stop_call_ms, lose=False)
            return smr, False
        kill_to_stop_ms = _ms(kill_mono) if kill_mono is not None else None
        duration_ms = _ms(t0)
        smr = StopMoveResult(
            ok=True,
            reason=reason,
            stop_path="service",
            duration_ms=duration_ms,
            kill_to_stop_ms=kill_to_stop_ms,
            stop_call_ms=sent.stop_call_ms,
            state_after=self._state_after(),
        )
        return smr, False

    def _fallback(
        self,
        reason: StopCause,
        t0: float,
        kill_mono: float | None,
        *,
        service_error: str,
        stop_call_ms: float | None = None,
        lose: bool,
    ) -> StopMoveResult:
        """The fresh-process ``stop_move``; with ``lose``, first close the control connection and SIGKILL the
        service. Never raises: any failure is ``ok=False`` with the error in stderr_tail."""
        if lose:
            self._close_control()
            self.robot.kill()
        try:
            rc, response, tail, _ = self._run_utility(stop_move_utility)
        except Exception as e:  # noqa: BLE001 - the stop path must not raise (docs/safety.md)
            rc, response, tail = None, None, _stderr_tail(_error_text(e))
        return StopMoveResult(
            ok=response is not None and response.status == "ok",
            reason=reason,
            stop_path="fallback",
            duration_ms=_ms(t0),
            kill_to_stop_ms=_ms(kill_mono) if kill_mono is not None else None,
            stop_call_ms=stop_call_ms,
            service_error=service_error,
            service_lost=lose,
            state_after=response.state_after if response is not None else None,
            exit_code=rc,
            response=response,
            stderr_tail=tail,
        )

    def _state_after(self) -> RobotState | None:
        try:
            return self.robot.read_state()
        except StateReadError:
            return None

    def read_state(self) -> RobotState | None:
        """The ``read_state`` utility's state, read with its own DDS, not through the robot service (diagnosis)."""
        _, response, _, _ = self._run_utility(read_state_utility)
        return response.state_after if response is not None else None

    # --- control connection (skills/docs/skills.md) -------------------------------------------

    def start_robot(self) -> None:
        """Start the robot service and open the control connection; raises RobotServiceError."""
        try:
            self.robot.start()
            with self._control_lock:
                self._control()
        except (ServiceUnavailable, Refused) as e:
            raise RobotServiceError(f"Robot service control connection failed: {_error_text(e)}") from None

    def _control(self) -> RobotClient:
        """The control connection, opened if needed (caller holds _control_lock); it never starts the service.
        Raises RobotServiceError, ServiceUnavailable or Refused."""
        if self._client is None:
            if self.robot.pid is None:
                raise RobotServiceError("Robot service not running.")
            self._client = RobotClient.connect(self.robot.socket_path, control=True)
        return self._client

    def _ping(self) -> str | None:
        """None if the service answers within STOP_WAIT_S, else why not."""
        try:
            with self._control_lock:
                self._control().ping(STOP_WAIT_S)
        except _SERVICE_ERRORS as e:
            return _error_text(e)
        return None

    def _disarm(self) -> tuple[StopMoveResult | None, bool]:
        """End the step's lease. Returns the stop path run if the service was lost or reports a failed StopMove of
        its own (None if neither), and whether the service was lost (and killed)."""
        t0 = time.monotonic()
        try:
            with self._control_lock:
                failed = self._control().disarm()
        except _SERVICE_ERRORS as e:
            error = _error_text(e)
            return self._fallback(
                StopCause.ROBOT_SERVICE_LOST, time.monotonic(), None, service_error=error, lose=True
            ), True
        if failed is None:
            return None, False
        error = f"StopMove returned {failed.ret} ({failed.reason})"  # like a failed stop_sent: fallback, service kept
        cause = StopCause.SERVICE_STOP_FAILED
        return self._fallback(cause, t0, None, service_error=error, stop_call_ms=failed.stop_call_ms, lose=False), False

    def _close_control(self) -> None:
        with self._control_lock:
            client, self._client = self._client, None
        if client is not None:
            client.close()  # the service sends StopMove when the control connection closes

    # --- robot service (docs/running.md) ----------------------------------------------------------

    def fresh_state(self, *, start: bool = True) -> RobotState:
        if start and self.robot.pid is None:  # a dead service's connection is useless; the next arm opens a new one
            self._close_control()
        return self.robot.read_state(start=start)

    def state_stream(self, path: Path, t_start: float, observer: TaskObserver | None = None) -> StateStream:
        return StateStream(self.robot.socket_path, path, t_start, observer)

    def close(self) -> None:
        """Close the control connection, stop the vision and robot services (docs/running.md); idempotent."""
        self._close_control()
        self.vision.stop()
        self.robot.stop()
