"""The executor: runs one step as a skill process under a robot service lease and watches it until it exits
(docs/safety.md, dispatcher/docs/execution.md). It receives its parts; ``dispatcher.app`` wires them."""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, cast

from contract.faults import ROBOT_FAULTS, SKILL_FAULTS, RobotFault, write_fault
from services.robot.protocol import MAX_TIMEOUT_S

from .. import prompts
from ..config import Config
from ..models import ExecResult, RobotState, StopCause, StopMoveResult
from ..registry import SkillDescriptor
from .control import LIVENESS_PERIOD_S, ControlLink, ServiceLost
from .env import ChildEnv
from .kill import KillSwitch
from .outcome import step_outcome
from .process import SkillProcess
from .robot_service import RobotSupervisor
from .state_stream import StateStream
from .stop_path import StopPath
from .vision_service import VisionSupervisor

if TYPE_CHECKING:
    from ..runlog.sinks import Observer

POLL_INTERVAL_S = 0.05  # wait-loop poll period (docs/safety.md)


def _ms(t0: float) -> float:
    return (time.monotonic() - t0) * 1000.0


class Executor:
    """Implements ``SkillExecutor``: one step at a time, with the services it owns (docs/running.md)."""

    def __init__(
        self,
        cfg: Config,
        env: ChildEnv,
        robot: RobotSupervisor,
        vision: VisionSupervisor,
        control: ControlLink,
        stop_path: StopPath,
        cwd: Path,
    ):
        self.cfg = cfg
        self.robot = robot
        self.vision = vision
        self._env = env
        self._control = control
        self._stop_path = stop_path
        self._cwd = Path(cwd)
        self._switch = KillSwitch()

    def start_robot(self) -> None:
        """Start the robot service and open the control connection; raises RobotServiceError."""
        self.robot.start()
        self._control.open()

    def kill_current(self, cause: StopCause) -> bool:
        """Kill the running skill process, if any; returns whether one was killed."""
        return self._switch.kill_current(cause)

    def stop_move(self, reason: StopCause) -> StopMoveResult:
        return self._stop_path.stop(reason)

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
        if skill.uses_vision:  # the wait counts against the task, not the step
            remaining_task_s -= self.vision.ensure_ready(stop_event, remaining_task_s)
        t0 = time.monotonic()
        token = secrets.token_hex(16)
        if fault in ROBOT_FAULTS:  # the sim takes it at this arm (services/robot/docs/service.md#fault-injection)
            self._write_robot_fault(fault)
        try:
            self._control.arm(token, min(timeout_s, MAX_TIMEOUT_S))
        except ServiceLost as e:  # never started: the fallback stop, then a service-lost step
            stop = self._stop_path.lost(e.reason, t0)
            return ExecResult(
                outcome="error",
                error_code=StopCause.ROBOT_SERVICE_LOST.value,
                error_message=prompts.KILL_ROBOT_SERVICE_LOST,
                stop_move=stop,
                duration_ms=_ms(t0),
            )
        skill_fault = fault if fault in SKILL_FAULTS else None
        proc = SkillProcess(skill.entrypoint, params, self._env.for_skill(skill_fault, token), self._cwd)
        self._switch.register(proc)

        lost: str | None = None  # why the service counts as lost, once it does
        stop: StopMoveResult | None = None
        next_ping = t0 + LIVENESS_PERIOD_S
        while True:
            rc, kill = self._switch.poll(proc)
            if kill is not None and stop is None:  # stop now, in parallel with the SIGKILL (docs/safety.md)
                stop = self._stop_path.after_kill(kill, lost)
            if rc is not None:
                self._switch.release()
                break
            now = time.monotonic()
            elapsed = now - t0
            if kill is not None:
                pass  # killed and stopped: wait for the exit
            elif now >= next_ping:
                next_ping = now + LIVENESS_PERIOD_S
                try:
                    self._control.ping()
                except ServiceLost as e:  # SIGKILL the service and the skill, then the fallback stop above
                    lost = e.reason
                    self.robot.kill()
                    self._switch.kill(proc, StopCause.ROBOT_SERVICE_LOST)
            elif stop_event.is_set():
                self._switch.kill(proc, StopCause.OPERATOR)
            elif elapsed >= remaining_task_s:
                self._switch.kill(proc, StopCause.TASK_TIME_LIMIT)
            elif elapsed >= timeout_s:
                self._switch.kill(proc, StopCause.STEP_TIMEOUT)
            time.sleep(POLL_INTERVAL_S)

        if stop is None and lost is not None:  # lost after the skill had exited: nothing was killed
            stop = self._stop_path.lost(lost, time.monotonic())
        elif stop is None:  # no stop sent by the executor: the disarm reports the service's own failed StopMove
            stop = self._stop_path.disarm()
        proc.join()
        duration_ms = _ms(t0)  # stops before the reply is parsed (dispatcher/docs/execution.md)
        run = proc.finish(skill.name)

        service_lost = stop is not None and stop.service_lost
        verdict = step_outcome(kill.cause if kill else None, service_lost, run.response, rc, timeout_s)
        return ExecResult(
            outcome=verdict.outcome,
            interrupt_cause=verdict.interrupt_cause,
            error_code=verdict.error_code,
            error_message=verdict.error_message,
            duration_ms=duration_ms,
            process=run,
            stop_move=stop,
        )

    def _write_robot_fault(self, kind: str) -> None:
        """Put ``kind`` in the stub fault file for the next arm; faults are stub-only (config-validated)."""
        if self.cfg.robot.backend != "stub":
            raise RuntimeError(f"stub fault {kind!r} under the {self.cfg.robot.backend} backend")
        self.cfg.stub.state_file.parent.mkdir(parents=True, exist_ok=True)
        write_fault(self.cfg.stub.state_file, cast(RobotFault, kind))

    def fresh_state(self, *, start: bool = True) -> RobotState:
        if start and self.robot.pid is None:  # a dead service's connection is useless; the next arm opens a new one
            self._control.close()
        return self.robot.read_state(start=start)

    def state_stream(self, path: Path, t_start: float, observers: Sequence[Observer] = ()) -> StateStream:
        return StateStream(self.robot.socket_path, path, t_start, observers)

    def close(self) -> None:
        """Close the control connection, stop the vision and robot services (docs/running.md); idempotent."""
        self._control.close()
        self.vision.stop()
        self.robot.stop()
