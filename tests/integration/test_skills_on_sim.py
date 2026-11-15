"""The walk and turn skills driven through the robot service motion loop on the sim (services/robot/docs/service.md)."""

from __future__ import annotations

import math
import threading

import pytest

from contract.env import STUB_FAULT, STUB_STATE_FILE, STUB_TIME_SCALE
from services.backend import stub_state
from services.robot import protocol, service
from services.robot.backends import NoArmHook
from services.robot.backends import sim as sim_backend
from services.robot.protocol import MoveTarget
from services.robot.state_store import StateStore
from skills import turn, walk
from tests.helpers.fake_clock import Clock


@pytest.fixture
def state(tmp_path, monkeypatch):
    """A stub with its state file in tmp_path, no time scale, no fault and no running command."""
    path = tmp_path / "logs" / "stub_state.json"
    monkeypatch.setenv(STUB_STATE_FILE, str(path))
    monkeypatch.setenv(STUB_TIME_SCALE, "0")
    monkeypatch.delenv(STUB_FAULT, raising=False)
    return path


def run_move(vx: float, vy: float, vyaw: float, duration_s: float, target=None, *, publish: bool) -> dict:
    """One move primitive on the robot service's loop over SimRobot; ``publish``: a state after every tick, as the
    sim service does, else no state at all."""
    sim, store = sim_backend.SimRobot(), StateStore(lambda msg: msg, clock=Clock())

    def wait(seconds, abort):
        sim.wait(seconds, abort)
        if publish:
            store.put(sim_backend.sample_state())

    if publish:
        store.put(sim_backend.sample_state())
    m = service.Motion(sim, wait, store, NoArmHook())
    threading.Thread(target=m._loop, daemon=True).start()
    job = service.Job(run=m.move(vx, vy, vyaw, duration_s, target))
    m._jobs.put(job)
    assert job.done.wait(5) and job.reply is not None
    return job.reply


def test_stub_walk_covers_its_distance(state):
    params = {"distance_m": 2.55}
    run_move(walk.VELOCITY_MPS, 0.0, 0.0, walk._motion_s(params), publish=False)
    pose = stub_state.read_state(state).pose
    assert (pose.x, pose.y, pose.yaw) == pytest.approx((2.55, 0.0, 0.0))


def test_stub_turn_covers_its_angle_open_and_closed_loop(state):
    params, yaw = {"angle_deg": 90.0}, math.radians(90.0)
    target = MoveTarget(yaw_rad=yaw, lead_s=turn.LEAD_S)
    tick_rad = protocol.CMD_PERIOD_S * turn.YAW_RATE_RPS
    run_move(0.0, 0.0, turn.YAW_RATE_RPS, turn._motion_s(params), target, publish=False)  # no yaw: open loop
    assert stub_state.read_state(state).pose.yaw == pytest.approx(yaw, abs=tick_rad / 2)  # whole periods
    stub_state.reset("standing", state)
    reply = run_move(0.0, 0.0, turn.YAW_RATE_RPS, turn._motion_s(params), target, publish=True)
    assert reply["duration_s"] < turn._motion_s(params)  # stopped on the measured yaw, before the cap
    assert yaw - service.YAW_STOP_MARGIN_RAD <= reply["measured"]["yaw_rad"] < yaw + tick_rad
