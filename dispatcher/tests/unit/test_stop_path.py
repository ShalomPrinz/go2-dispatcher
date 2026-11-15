"""The stop path on a fake control link: service stop, fallback, service loss; never raises (docs/safety.md)."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from contract.runtime import SkillOutcome, StateSampler, build_response, to_json
from contract.schema import RobotState
from dispatcher.execution import stop_path
from dispatcher.execution.control import ControlLink, ServiceLost
from dispatcher.execution.env import ChildEnv
from dispatcher.execution.kill import Kill
from dispatcher.execution.process import UtilityRun, parse_response
from dispatcher.execution.robot_service import RobotSupervisor
from dispatcher.models import StopCause
from services.robot.protocol import StopFailure, StopSent

STANDING = RobotState(t=0.0, backend="stub", posture="standing")
TIMEOUT = "ServiceUnavailable: no stop_sent within 0.5s"


class FakeLink:
    """A ControlLink: ``stop`` returns ``sent`` or raises ``lost``; records the calls in ``calls``."""

    def __init__(self, sent: StopSent | None = None, lost: str | None = None, failed: StopFailure | None = None):
        self.sent, self.lost, self.failed = sent, lost, failed
        self.calls: list[str] = []

    def stop(self) -> StopSent:
        self.calls.append("stop")
        if self.lost is not None:
            raise ServiceLost(self.lost)
        assert self.sent is not None
        return self.sent

    def disarm(self) -> StopFailure | None:
        self.calls.append("disarm")
        if self.lost is not None:
            raise ServiceLost(self.lost)
        return self.failed

    def close(self) -> None:
        self.calls.append("close")


class FakeRobot:
    def __init__(self, calls: list[str]):
        self.calls = calls

    def kill(self) -> None:
        self.calls.append("kill_service")

    def read_state(self) -> RobotState:
        self.calls.append("read_state")
        return STANDING


class FakeEnv:
    def for_service(self) -> dict[str, str]:
        return {}


def _ok_reply() -> UtilityRun:
    line = to_json(build_response("stop_move", SkillOutcome.ok(observations={}, timing={}), StateSampler(_state), 0.0))
    return UtilityRun(exit_code=0, response=parse_response(line, "stop_move"), stderr_tail=None)


def _state() -> RobotState:
    return STANDING


@pytest.fixture
def fallback_runs(monkeypatch):
    """Replaces the fallback process with a confirmed stop; returns the list of calls into it."""
    runs: list[str] = []

    def run_utility(utility, env, cwd):
        runs.append(utility.policy.name)
        return _ok_reply()

    monkeypatch.setattr(stop_path, "run_utility", run_utility)
    return runs


def _path(link: FakeLink) -> stop_path.StopPath:
    robot = cast(RobotSupervisor, FakeRobot(link.calls))
    return stop_path.StopPath(cast(ControlLink, link), robot, cast(ChildEnv, FakeEnv()), Path("."))


def test_stop_is_sent_through_the_service(fallback_runs):
    link = FakeLink(sent=StopSent(ret=0, stop_call_ms=3.0))
    kill = Kill(StopCause.OPERATOR, at_mono=0.0)
    smr = _path(link).stop(StopCause.OPERATOR, kill)
    assert (smr.ok, smr.stop_path, smr.stop_call_ms, smr.service_lost) == (True, "service", 3.0, False)
    assert smr.kill_to_stop_ms is not None and smr.state_after == STANDING
    assert link.calls == ["stop", "read_state"] and fallback_runs == []


def test_no_stop_sent_in_time_loses_the_service_then_falls_back(fallback_runs):
    link = FakeLink(lost=TIMEOUT)
    smr = _path(link).stop(StopCause.STEP_TIMEOUT)
    assert (smr.ok, smr.reason, smr.stop_path, smr.service_lost) == (True, "step_timeout", "fallback", True)
    assert smr.service_error == TIMEOUT and smr.kill_to_stop_ms is None
    assert link.calls == ["stop", "close", "kill_service"] and fallback_runs == ["stop_move"]


def test_a_failed_service_stop_falls_back_and_keeps_the_service(fallback_runs):
    link = FakeLink(sent=StopSent(ret=5, stop_call_ms=2.0))
    smr = _path(link).stop(StopCause.OPERATOR)
    assert (smr.stop_path, smr.service_lost, smr.service_error, smr.stop_call_ms) == (
        "fallback",
        False,
        "StopMove returned 5",
        2.0,
    )
    assert link.calls == ["stop"] and fallback_runs == ["stop_move"]


def test_a_kill_after_the_loss_runs_the_fallback_without_a_service_stop(fallback_runs):
    link = FakeLink(sent=StopSent(ret=0, stop_call_ms=1.0))
    kill = Kill(StopCause.ROBOT_SERVICE_LOST, at_mono=0.0)
    smr = _path(link).after_kill(kill, lost="ServiceUnavailable: no pong")
    assert (smr.reason, smr.stop_path, smr.service_lost) == ("robot_service_lost", "fallback", True)
    assert link.calls == ["close", "kill_service"]


def test_disarm(fallback_runs):
    assert _path(FakeLink()).disarm() is None and fallback_runs == []
    failed = StopFailure(reason="lease_deadline", ret=99, stop_call_ms=4.0)
    smr = _path(FakeLink(failed=failed)).disarm()
    assert smr is not None and (smr.reason, smr.service_lost) == ("service_stop_failed", False)
    assert smr.service_error == "StopMove returned 99 (lease_deadline)"
    lost = _path(FakeLink(lost="ServiceUnavailable: EOF")).disarm()
    assert lost is not None and (lost.reason, lost.service_lost) == ("robot_service_lost", True)


def test_the_fallback_never_raises(monkeypatch):
    def boom(utility, env, cwd):
        raise OSError("cannot spawn")

    monkeypatch.setattr(stop_path, "run_utility", boom)
    smr = _path(FakeLink(lost=TIMEOUT)).stop(StopCause.INTERNAL_ERROR)
    assert (smr.ok, smr.exit_code, smr.stderr_tail) == (False, None, "OSError: cannot spawn")


def test_a_dead_service_is_a_lost_link(tmp_path):
    """ControlLink turns every failure of the connection into ServiceLost; it never starts the service."""
    robot = RobotSupervisor(tmp_path, tmp_path / "robot.log", str(tmp_path / "robot.sock"), dict)
    with pytest.raises(ServiceLost, match="RobotServiceError: Robot service not running."):
        ControlLink(robot).stop()
