"""FakeClock and FakeExecutor for dispatcher loop tests (docs/testing.md)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from typing import Any

from dispatcher.executor import ExecResult
from dispatcher.models import RobotState, SkillResponse, StopMoveResult

FAKE_CLOCK_START = 1000.0


class FakeClock:
    """Manual monotonic clock: ``now()`` only moves on ``advance(seconds)``."""

    def __init__(self, start: float = FAKE_CLOCK_START):
        self._t = start
        self._lock = threading.Lock()

    def now(self) -> float:
        with self._lock:
            return self._t

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._t += seconds


def state(posture: str) -> RobotState:
    return RobotState(t=time.time(), backend="stub", posture=posture)


def exec_result(outcome: str = "ok", *, skill: str = "walk",
                observations: dict | None = None, posture: str | None = None,
                error_code: str | None = None, error_message: str | None = None,
                interrupt_cause: str | None = None,
                stop_move: StopMoveResult | None = None) -> ExecResult:
    """An ``ExecResult``. ``ok``/``error`` carry a response (with ``state_after`` if
    ``posture`` is given); other outcomes carry none."""
    response = None
    if outcome in ("ok", "error"):
        if outcome == "error":
            error_code = error_code or "sdk_error"
            error_message = error_message or "Move returned 1"
        response = SkillResponse(
            schema_version=1, skill=skill, status=outcome, observations=observations or {},
            error={"code": error_code, "message": error_message} if outcome == "error" else None,
            state_after=state(posture) if posture else None)
    elif outcome != "ok" and error_code is None:
        error_code, error_message = outcome, error_message or f"step {outcome}"
    return ExecResult(outcome=outcome, interrupt_cause=interrupt_cause, response=response,
                      exit_code=0, pid=12345, duration_ms=1.0, error_code=error_code,
                      error_message=error_message, stop_move=stop_move)


def stop_move_result(reason: str = "operator", *, ok: bool = True,
                     posture: str | None = None) -> StopMoveResult:
    response = None
    if posture is not None:
        response = SkillResponse(schema_version=1, skill="stop_move", status="ok",
                                 state_after=state(posture))
    return StopMoveResult(ok=ok, reason=reason, duration_ms=1.0, exit_code=0 if ok else 1,
                          response=response)


ScriptItem = ExecResult | BaseException | Callable[[dict], ExecResult]


class FakeExecutor:
    """Executor stand-in returning scripted results, one per ``run()``:

    - an ``ExecResult`` -> returned;
    - an exception instance -> raised;
    - a callable ``f(call_kwargs) -> ExecResult`` -> called (to block, set the stop
      event, advance a clock, ...).

    Records ``runs`` (kwargs dicts incl. ``skill`` and ``params``), ``kills`` (causes)
    and ``stop_moves`` (reasons). ``stop_move`` returns ok unless ``stop_move_ok=False``;
    its ``state_after`` posture is ``stop_move_posture`` (none if ``None``).
    ``on_kill(cause)`` runs on every ``kill_current``.
    """

    def __init__(self, results: Sequence[ScriptItem] = (), *, stop_move_ok: bool = True,
                 stop_move_posture: str | None = None,
                 on_kill: Callable[[str], None] | None = None):
        self.results = list(results)
        self.stop_move_ok = stop_move_ok
        self.stop_move_posture = stop_move_posture
        self.on_kill = on_kill
        self.runs: list[dict[str, Any]] = []
        self.kills: list[str] = []
        self.stop_moves: list[str] = []

    def run(self, skill, params: dict, *, fault, timeout_s: float, remaining_task_s: float,
            stop_event: threading.Event) -> ExecResult:
        call = {"skill": skill.name, "params": params, "fault": fault, "timeout_s": timeout_s,
                "remaining_task_s": remaining_task_s, "stop_event": stop_event}
        n = len(self.runs)
        self.runs.append(call)
        if n >= len(self.results):
            raise AssertionError(f"FakeExecutor.run called {n + 1} times; "
                                 f"only {len(self.results)} results scripted")
        item = self.results[n]
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, ExecResult):
            return item
        return item(call)

    def kill_current(self, cause: str) -> bool:
        self.kills.append(cause)
        if self.on_kill is not None:
            self.on_kill(cause)
        return False

    def stop_move(self, reason: str) -> StopMoveResult:
        self.stop_moves.append(reason)
        return stop_move_result(reason, ok=self.stop_move_ok, posture=self.stop_move_posture)

    def read_state(self) -> RobotState | None:
        return None
