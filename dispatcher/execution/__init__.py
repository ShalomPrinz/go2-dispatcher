"""Execution: the executor and its parts, the robot and vision service supervisors and the state stream
(dispatcher/docs/execution.md). ``SkillExecutor`` is what the loop needs; ``dispatcher.app`` wires the real one."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from ..models import ExecResult, RobotState, StopCause, StopMoveResult
from ..registry import SkillDescriptor
from .executor import Executor

if TYPE_CHECKING:
    from ..runlog.sinks import TaskObserver

__all__ = ["Executor", "SkillExecutor", "StreamHandle"]


class SkillExecutor(Protocol):
    """What the dispatcher needs from an executor; ``Executor`` or a test fake."""

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
