"""Shared skill helpers in process: policy and run_skill (skills/docs/skills.md); the runtime itself is tested in
contract/tests/unit/test_runtime.py. One subprocess case per mechanism stays in tests/integration."""

from __future__ import annotations

import math
import sys

import pytest

from contract import env, process, runtime
from contract.policy import MotionCost
from contract.schema import RobotState
from services.robot import client as robot_client
from skills import runner, sit, turn, walk


def test_policy_resolves_constants_and_functions():
    p = {"direction": "forward", "distance_m": 1.5}
    assert walk.POLICY.timeout_s(p) == 10.0 + 1.5 * (1.5 / walk.VELOCITY_MPS + walk.LEAD_S)
    turn_s = math.radians(90) / turn.YAW_RATE_RPS * turn.CAP_FACTOR + turn.CAP_S
    assert turn.POLICY.timeout_s({"direction": "left", "angle_deg": 90}) == 10.0 + 1.5 * turn_s
    assert walk.POLICY.motion_cost(p) == MotionCost(distance_m=1.5)
    assert sit.POLICY.timeout_s({}) == sit.TIMEOUT_S
    assert sit.POLICY.motion_cost({}) == MotionCost()
    assert type(MotionCost(rotation_deg=90).rotation_deg) is float


def test_run_skill_keeps_state_sampled_before_an_exception(monkeypatch):
    """run_skill samples through the robot service; the state taken before the body raised survives."""
    state = RobotState(t=1.0, backend="stub", posture="standing", body_height=0.32)
    monkeypatch.setattr(robot_client.session, "read_state", lambda: state)
    monkeypatch.setattr(robot_client.session, "client", lambda: None)  # connected
    monkeypatch.setenv(env.BACKEND, "stub")
    monkeypatch.setattr(process, "capture_stdout", lambda: None)
    monkeypatch.setattr(sys, "argv", ["walk", "{}"])
    sent = []

    def emit(skill, outcome, states, total_ms, setup):
        sent.append(runtime.build_response(skill, outcome, states, total_ms, setup=setup))
        raise SystemExit

    def body(params):
        raise RuntimeError("boom\nsecond line")

    monkeypatch.setattr(runtime, "emit", emit)
    with pytest.raises(SystemExit):
        runner.run_skill(walk.POLICY, body)
    response = sent[0]
    assert response.error is not None and response.error.message == "RuntimeError: boom"
    assert (response.state_before, response.state_after) == (state, None)
    assert list(response.timing) == ["state_ms", "total_ms"]
