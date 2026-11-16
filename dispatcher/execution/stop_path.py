"""The stop path: the service's ``stop``, else the fresh-process ``stop_move`` fallback; never raises
(docs/safety.md, dispatcher/docs/execution.md)."""

from __future__ import annotations

import time
from pathlib import Path

from services.robot.protocol import STOP_MOVE

from ..models import RobotState, StateReadError, StopCause, StopMoveResult
from .control import ControlLink, ServiceLost, error_text
from .env import ChildEnv
from .kill import Kill
from .process import UtilityRun, run_utility, stderr_tail
from .robot_service import RobotSupervisor


def _ms(t0: float) -> float:
    return (time.monotonic() - t0) * 1000.0


class StopPath:
    """Every stop the executor sends, each returning one ``StopMoveResult``. ``service_lost`` on the result is
    the one record that the service was lost and killed on this stop."""

    def __init__(self, control: ControlLink, robot: RobotSupervisor, env: ChildEnv, cwd: Path):
        self._control = control
        self._robot = robot
        self._env = env
        self._cwd = Path(cwd)

    def stop(self, reason: StopCause, kill: Kill | None = None) -> StopMoveResult:
        """The service's ``stop``; the fallback when no ``stop_sent`` arrives in STOP_WAIT_S (the service is then
        lost) or its StopMove failed (the service is kept)."""
        t0 = time.monotonic()
        try:
            sent = self._control.stop()
        except ServiceLost as e:
            return self.fallback(reason, t0, kill, service_error=e.reason, lose=True)
        if sent.ret != 0:  # the service answered but its StopMove failed: the fallback has its own DDS
            error = f"StopMove returned {sent.ret}"
            return self.fallback(reason, t0, kill, service_error=error, stop_call_ms=sent.stop_call_ms, lose=False)
        kill_to_stop_ms = _ms(kill.at_mono) if kill is not None else None
        duration_ms = _ms(t0)
        return StopMoveResult(
            ok=True,
            reason=reason,
            stop_path="service",
            duration_ms=duration_ms,
            kill_to_stop_ms=kill_to_stop_ms,
            stop_call_ms=sent.stop_call_ms,
            state_after=self._state_after(),
        )

    def after_kill(self, kill: Kill, lost: str | None) -> StopMoveResult:
        """The stop for a killed skill: the fallback when the service is already ``lost`` (why), else ``stop``."""
        if lost is not None:
            return self.lost(lost, kill.at_mono, kill)
        return self.stop(kill.cause, kill)

    def disarm(self) -> StopMoveResult | None:
        """End the step's lease. The fallback if the service is lost, or reports a failed StopMove of its own
        (the service is then kept); None if neither."""
        t0 = time.monotonic()
        try:
            failed = self._control.disarm()
        except ServiceLost as e:
            return self.lost(e.reason, time.monotonic())
        if failed is None:
            return None
        error = f"StopMove returned {failed.ret} ({failed.reason})"  # like a failed stop_sent: fallback, service kept
        cause = StopCause.SERVICE_STOP_FAILED
        return self.fallback(cause, t0, None, service_error=error, stop_call_ms=failed.stop_call_ms, lose=False)

    def lost(self, service_error: str, t0: float, kill: Kill | None = None) -> StopMoveResult:
        """The fallback for a lost service, as its own cause (``robot_service_lost``)."""
        return self.fallback(StopCause.ROBOT_SERVICE_LOST, t0, kill, service_error=service_error, lose=True)

    def fallback(
        self,
        reason: StopCause,
        t0: float,
        kill: Kill | None,
        *,
        service_error: str,
        stop_call_ms: float | None = None,
        lose: bool,
    ) -> StopMoveResult:
        """The fresh-process ``stop_move``; with ``lose``, first close the control connection and SIGKILL the
        service. Never raises: any failure is ``ok=False`` with the error in stderr_tail."""
        if lose:
            self._control.close()
            self._robot.kill()
        try:
            run: UtilityRun | None = run_utility(STOP_MOVE, self._env.for_service(), self._cwd)
        except Exception as e:  # noqa: BLE001 - the stop path must not raise (docs/safety.md)
            run, tail = None, stderr_tail(error_text(e))
        else:
            tail = run.stderr_tail
        response = run.response if run is not None else None
        return StopMoveResult(
            ok=response is not None and response.status == "ok",
            reason=reason,
            stop_path="fallback",
            duration_ms=_ms(t0),
            kill_to_stop_ms=_ms(kill.at_mono) if kill is not None else None,
            stop_call_ms=stop_call_ms,
            service_error=service_error,
            service_lost=lose,
            state_after=response.state_after if response is not None else None,
            exit_code=run.exit_code if run is not None else None,
            response=response,
            stderr_tail=tail,
        )

    def _state_after(self) -> RobotState | None:
        try:
            return self._robot.read_state()
        except StateReadError:
            return None
