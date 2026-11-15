"""Execution: the executor and its parts, the robot and vision service supervisors and the state stream
(dispatcher/docs/execution.md). ``SkillExecutor`` is what the loop and transports need; ``build_executor`` wires
the real one."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from contract.env import robot_socket_path
from services.robot.protocol import READ_STATE

from ..config import Config
from ..models import ExecResult, RobotState, StopCause, StopMoveResult
from ..registry import SkillDescriptor
from .control import ControlLink
from .env import ChildEnv
from .executor import Executor
from .process import run_utility
from .robot_service import RobotSupervisor
from .robot_service import log_path as robot_log_path
from .stop_path import StopPath
from .vision_service import VisionSupervisor
from .vision_service import log_path as vision_log_path

if TYPE_CHECKING:
    from ..runlog import TaskObserver

__all__ = ["Executor", "SkillExecutor", "StreamHandle", "build_executor", "read_state_utility"]


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


def build_executor(cfg: Config, cwd: Path | None = None) -> Executor:
    """The executor with its parts; processes start in ``cwd`` (default ``cfg.base_dir``). Starts nothing."""
    cwd = Path(cwd if cwd is not None else cfg.base_dir)
    env = ChildEnv(cfg)
    vision = VisionSupervisor(cwd, vision_log_path(cfg.log.dir), env.for_service)
    robot = RobotSupervisor(cwd, robot_log_path(cfg.log.dir), robot_socket_path(cfg.log.dir), env.for_service)
    control = ControlLink(robot)
    return Executor(cfg, env, robot, vision, control, StopPath(control, robot, env, cwd), cwd)


def read_state_utility(cfg: Config) -> RobotState | None:
    """The ``read_state`` utility's state, read with its own DDS, not through the robot service (diagnosis)."""
    response = run_utility(READ_STATE, ChildEnv(cfg).for_service(), cfg.base_dir).response
    return response.state_after if response is not None else None
