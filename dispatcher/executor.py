"""Executor: one subprocess per skill call, timeouts, kill, StopMove (docs/safety.md)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import ModuleType
from typing import Literal, Protocol

from pydantic import BaseModel, TypeAdapter, ValidationError

from skills import read_state as read_state_utility
from skills import stop_move as stop_move_utility
from skills.env import child_env
from skills.policy import SkillPolicy

from .config import Config
from .models import RobotState, SkillResponse, StopCause, StopMoveResult
from .registry import SkillDescriptor

SECRET_ENV = frozenset({"ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN"})

POLL_INTERVAL_S = 0.05  # wait-loop poll period (docs/safety.md)
READER_JOIN_TIMEOUT_S = 2.0  # join timeout per reader thread (docs/safety.md)
STDERR_TAIL_CHARS = 2000  # stderr kept for the log (skills/docs/skills.md)

_RESPONSE_ADAPTER = TypeAdapter(SkillResponse)  # built once; validates the stdlib dataclass

InterruptCause = StopCause  # old name, still imported by dispatcher/tests/helpers/fakes.py

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

    def stop_move(self, reason: StopCause) -> StopMoveResult: ...

    def read_state(self) -> RobotState | None: ...


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
                self._stderr = (self._stderr + chunk)[-STDERR_TAIL_CHARS:]
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
        return self._stderr or None


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


class Executor:
    def __init__(self, cfg: Config, base_dir: Path):
        self.cfg = cfg
        self.base_dir = Path(base_dir)
        self._lock = threading.Lock()
        self._current: subprocess.Popen | None = None
        self._kill_cause: StopCause | None = None

    # --- environment and process start (skills/docs/skills.md) ----------------------------------

    def _env(self, fault: str | None) -> dict[str, str]:
        return child_env(
            {k: v for k, v in os.environ.items() if k not in SECRET_ENV},
            backend=self.cfg.robot.backend,
            network_interface=self.cfg.robot.network_interface,
            yolo_weights=str(self.cfg.robot.yolo_weights),
            stub_state_file=str(self.cfg.stub.state_file),
            stub_time_scale=self.cfg.stub.time_scale,
            stub_detections=self.cfg.stub.detections,
            parent_pid=os.getpid(),
            fault=fault,
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
            _killpg(proc.pid)
            return True

    def _release_locked(self) -> StopCause | None:
        """Clear the current process (caller holds the lock); returns why it was killed, if it was."""
        cause, self._current, self._kill_cause = self._kill_cause, None, None
        return cause

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
        t0 = time.monotonic()
        proc = self._popen(skill.entrypoint, params, self._env(fault))
        readers = _Readers(proc)
        with self._lock:
            self._current = proc
            self._kill_cause = None

        while True:
            with self._lock:
                rc = proc.poll()
                if rc is not None:
                    cause = self._release_locked()
                    break
            elapsed = time.monotonic() - t0
            if stop_event.is_set():
                self._kill_locked(proc, StopCause.OPERATOR)
            elif elapsed >= remaining_task_s:
                self._kill_locked(proc, StopCause.TASK_TIME_LIMIT)
            elif elapsed >= timeout_s:
                self._kill_locked(proc, StopCause.STEP_TIMEOUT)
            time.sleep(POLL_INTERVAL_S)
        readers.join()
        duration_ms = _ms(t0)

        response = _parse_response(readers.stdout, skill.name)

        outcome: ExecOutcome
        interrupt_cause: StopCause | None = None
        stop: StopMoveResult | None = None
        if cause is not None:
            outcome, error_code, template = cause.kill
            stop = self.stop_move(reason=cause)
            interrupt_cause = cause if outcome == "interrupted" else None
            error_message = template.format(timeout_s=timeout_s)
        elif response is None:
            outcome, error_code = "malformed", "malformed"
            error_message = f"skill process exited with code {rc} without a valid response"
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
            exit_code=rc,
            pid=proc.pid,
            duration_ms=duration_ms,
            stderr_tail=readers.stderr_tail,
            response=response,
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
        tail = (err or "")[-STDERR_TAIL_CHARS:] or None
        return proc.returncode, response, tail, duration_ms

    def stop_move(self, reason: StopCause) -> StopMoveResult:
        """Never raises: any failure is returned as ``ok=False`` with the error in stderr_tail."""
        t0 = time.monotonic()
        try:
            rc, response, tail, duration_ms = self._run_utility(stop_move_utility)
        except Exception as e:  # noqa: BLE001 - the stop path must not raise (docs/safety.md)
            return StopMoveResult(
                ok=False,
                reason=reason,
                duration_ms=_ms(t0),
                stderr_tail=f"{type(e).__name__}: {e}"[-STDERR_TAIL_CHARS:],
            )
        ok = response is not None and response.status == "ok"
        return StopMoveResult(
            ok=ok, reason=reason, duration_ms=duration_ms, exit_code=rc, response=response, stderr_tail=tail
        )

    def read_state(self) -> RobotState | None:
        _, response, _, _ = self._run_utility(read_state_utility)
        return response.state_after if response is not None else None
