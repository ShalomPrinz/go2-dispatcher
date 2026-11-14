"""The skills' side of motion in process: the mapping of the robot service's primitive replies to outcomes
(robot/docs/service.md). The service's motion loop is tested in robot/tests/unit/test_motion_loop.py."""

from __future__ import annotations

import pytest

from contract import backend
from robot import client as robot_client
from robot.client import Refused, ServiceUnavailable
from robot.protocol import MoveResult, MoveTarget
from skills import motion


class FakeSession:
    def __init__(self, reply):
        self.reply = reply
        self.dropped = False

    def client(self):
        return self

    def drop(self):
        self.dropped = True

    def move(self, *args, **kwargs):
        if isinstance(self.reply, BaseException):
            raise self.reply
        return self.reply

    def action(self, *args, **kwargs):
        return self.move()


@pytest.fixture
def session(monkeypatch):
    """Install a FakeSession answering ``reply``; record backend sleeps."""
    sleeps: list[float] = []
    monkeypatch.setenv("GO2_BACKEND", "real")  # no stub faults
    monkeypatch.setattr(backend, "sleep", sleeps.append)

    def install(reply):
        fake = FakeSession(reply)
        monkeypatch.setattr(robot_client, "session", fake)
        return fake, sleeps

    return install


@pytest.mark.parametrize(
    "result, code, message, sdk_ret",
    [
        (MoveResult(2.0, 0, 0), None, None, 0),
        (MoveResult(0.2, 7, 5), "sdk_error", "Move returned 7; StopMove returned 5", 7),
        (MoveResult(0.3, 0, 4), "sdk_error", "StopMove returned 4", 4),
    ],
    ids=["ok", "move_fails", "stop_fails"],
)
def test_move_outcome(session, result, code, message, sdk_ret):
    session(result)
    o = motion.move(motion.Velocity(0.3, 0, 0), 2.0)
    assert (o.error_code, o.error_message) == (code, message)
    assert o.observations == {"duration_s": result.duration_s, "sdk_ret": sdk_ret}


@pytest.mark.parametrize(
    "yaw_rad, extra",
    [(-1.6, {"closed_loop": True, "turned_deg": 91.7}), (None, {"closed_loop": False, "turned_deg": None})],
    ids=["closed_loop", "fell_back"],
)
def test_move_outcome_with_a_yaw_target(session, yaw_rad, extra):
    session(MoveResult(1.6, 0, 0, yaw_rad=yaw_rad))
    o = motion.move(motion.Velocity(0, 0, -1.0), 2.5, MoveTarget(yaw_rad=-1.57, lead_s=0.2))
    assert o.status == "ok" and o.observations == {"duration_s": 1.6, "sdk_ret": 0, **extra}


@pytest.mark.parametrize(
    "error, code, message",
    [
        (Refused("no_lease", "no lease is armed"), "motion_rejected", "no_lease: no lease is armed"),
        (Refused("sdk_exception", "ValueError: x"), "exception", "ValueError: x"),
        (ServiceUnavailable("gone"), "robot_unavailable", "ServiceUnavailable: gone"),
    ],
    ids=["no_lease", "sdk_exception", "unavailable"],
)
def test_refused_primitive_drops_the_connection(session, error, code, message):
    fake, _ = session(error)
    o = motion.single_action("StandDown", 2.0)
    assert (o.status, o.error_code, o.error_message) == ("error", code, message)
    assert fake.dropped


def test_single_action_settles_only_on_success(session):
    _, sleeps = session(3)
    o = motion.single_action("StandDown", 2.0)
    assert (o.error_code, o.error_message, o.observations, sleeps) == (
        "sdk_error",
        "StandDown returned 3",
        {"sdk_ret": 3},
        [],
    )
    _, sleeps = session(0)
    assert motion.single_action("StandDown", 2.0).status == "ok" and sleeps == [2.0]
