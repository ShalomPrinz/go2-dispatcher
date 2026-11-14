"""The stub state file, the simulated sport client, its odometry, obstacle distances and robot faults
(services/robot/docs/service.md)."""

from __future__ import annotations

import json
import math
import threading

import pytest

from contract.env import STUB_FAULT, STUB_OBJECTS, STUB_STATE_FILE, STUB_TIME_SCALE
from contract.schema import ObstacleDistances
from services.robot import protocol, service, stub_state
from services.robot.backends import real
from services.robot.backends import sim as sim_backend
from services.robot.state_store import StateStore
from tests.helpers.fake_clock import Clock

WALK_MPS = 0.3  # a walking speed for the move primitive


@pytest.fixture
def state(tmp_path, monkeypatch):
    """A stub with its state file in tmp_path, no time scale, no fault and no running command."""
    path = tmp_path / "logs" / "stub_state.json"
    monkeypatch.setenv(STUB_STATE_FILE, str(path))
    monkeypatch.setenv(STUB_TIME_SCALE, "0")
    monkeypatch.delenv(STUB_FAULT, raising=False)
    return path


def test_reset_overwrites_posture_and_pose_and_rejects_unknown(state):
    stub_state.write_pose(stub_state.Pose(1.0, 2.0, 0.5), state)
    stub_state.reset("sitting", state)
    assert stub_state.read_state(state) == stub_state.StubState("sitting", stub_state.Pose())
    with pytest.raises(ValueError):
        stub_state.reset("lying", state)  # pyright: ignore[reportArgumentType]
    assert stub_state.read_posture(state) == "sitting"


def test_writers_merge_and_missing_pose_is_origin(state):
    assert stub_state.read_state(state) == stub_state.StubState(
        "standing", stub_state.Pose()
    )  # no file: standing at the origin
    stub_state.write_posture("sitting", state)  # creates the missing parent directory
    state.write_text(json.dumps({"posture": "sitting"}))
    assert stub_state.read_state(state) == stub_state.StubState("sitting", stub_state.Pose())
    stub_state.write_pose(stub_state.Pose(1.0, -2.0, 0.25), state)
    stub_state.write_posture("standing", state)
    stub_state.write_command((0.1, 0.0, 0.0), state)
    pose = {"x": 1.0, "y": -2.0, "yaw": 0.25}
    expected = {"posture": "standing", "pose": pose, "command": [0.1, 0.0, 0.0], "sequence_s": 0.0, "condition": None}
    assert json.loads(state.read_text()) == expected
    state.write_text(json.dumps({"posture": "standing", "pose": {"x": 1.0}}))
    with pytest.raises(ValueError, match="invalid stub state file"):
        stub_state.read_state(state)


def test_advance_body_frame():
    p = sim_backend.advance(stub_state.Pose(0.0, 0.0, math.pi / 2), (0.5, 0.0, 0.0), 2.0)  # forward while facing +y
    assert (p.x, p.y) == pytest.approx((0.0, 1.0))
    p = sim_backend.advance(stub_state.Pose(), (0.0, 0.3, 0.0), 1.0)  # +vy is left
    assert (p.x, p.y) == pytest.approx((0.0, 0.3))
    p = sim_backend.advance(stub_state.Pose(), (0.0, 0.0, 1.0), 1.5 * math.pi)  # +vyaw is counter-clockwise, wrapped
    assert (p.x, p.y, p.yaw) == pytest.approx((0.0, 0.0, -math.pi / 2))
    p = sim_backend.advance(stub_state.Pose(), (1.0, 0.0, 1.0), math.pi / 2)  # quarter circle of radius 1
    assert (p.x, p.y, p.yaw) == pytest.approx((1.0, 1.0, math.pi / 2))


def test_move_wait_stop_integrates_and_samples_pose(state):
    sim, abort = sim_backend.SimRobot(), threading.Event()
    assert sim.Move(0.5, 0.0, 0.0) == 0
    sim.wait(2.0 + sim_backend.LINEAR_DEAD_S, abort)  # unscaled seconds drive the pose, after the dead time
    moving = sim_backend.sample_state()
    assert moving.velocity == pytest.approx([0.5, 0.0, 0.0])
    assert sim_backend.SimRobot().StopMove() == 0  # another process's client: the command lives in the state file
    sim.wait(5.0, abort)
    at_rest = sim_backend.sample_state()
    assert at_rest.position == pytest.approx([1.0, 0.0, sim_backend.BODY_HEIGHT_STANDING_M])
    assert (at_rest.velocity, at_rest.yaw_speed, at_rest.imu_rpy) == ([0.0, 0.0, 0.0], 0.0, [0.0, 0.0, 0.0])
    stub_state.write_pose(stub_state.Pose(0.0, 0.0, math.pi / 2), state)
    assert sim.Move(0.5, 0.0, 0.0) == 0
    assert sim_backend.sample_state().velocity == pytest.approx([0.0, 0.5, 0.0])  # body frame to world frame


def test_dead_time_per_axis_restarts_only_after_stop_move(state):
    sim, abort = sim_backend.SimRobot(), threading.Event()
    assert sim.Move(0.5, 0.0, 1.0) == 0
    sim.wait(sim_backend.YAW_DEAD_S, abort)
    assert stub_state.read_state(state).pose == stub_state.Pose()  # no axis has started
    sim.wait(sim_backend.LINEAR_DEAD_S - sim_backend.YAW_DEAD_S, abort)  # yaw turns in place meanwhile
    pose = stub_state.read_state(state).pose
    assert (pose.x, pose.y, pose.yaw) == pytest.approx((0.0, 0.0, sim_backend.LINEAR_DEAD_S - sim_backend.YAW_DEAD_S))
    stub_state.write_pose(stub_state.Pose(), state)
    assert sim.Move(0.5, 0.0, 0.0) == 0  # a new command within the sequence keeps its progress
    sim.wait(1.0, abort)
    assert stub_state.read_state(state).pose.x == pytest.approx(0.5)
    assert sim.StopMove() == 0 and sim.Move(0.5, 0.0, 0.0) == 0  # StopMove ends the sequence
    sim.wait(sim_backend.LINEAR_DEAD_S, abort)
    assert stub_state.read_state(state).pose.x == pytest.approx(0.5)


def run_move(
    vx: float, vy: float, vyaw: float, duration_s: float, target=None, *, publish: bool, fault: str | None = None
) -> dict:
    """One move primitive on the robot service's loop over SimRobot; ``publish``: a state after every tick, as the
    sim service does, else no state at all."""
    sim, store = sim_backend.SimRobot(), StateStore(lambda msg: msg, clock=Clock())

    def wait(seconds, abort):
        sim.wait(seconds, abort)
        if publish:
            store.put(sim_backend.sample_state())

    if publish:
        store.put(sim_backend.sample_state())
    m = service.Motion(sim, wait, store, sim=True)
    threading.Thread(target=m._loop, daemon=True).start()
    job = service.Job(run=m.move(vx, vy, vyaw, duration_s, target, fault))
    m._jobs.put(job)
    assert job.done.wait(5) and job.reply is not None
    return job.reply


def test_move_while_sitting_stores_no_command(state):
    stub_state.reset("sitting", state)
    sim = sim_backend.SimRobot()
    assert sim.Move(0.5, 0.0, 0.0) == sim_backend.STUB_ERR_NOT_STANDING
    sim.wait(2.0, threading.Event())
    assert stub_state.read_state(state) == stub_state.StubState("sitting", stub_state.Pose())


def test_stand_down_clears_the_command(state):
    sim = sim_backend.SimRobot()
    assert sim.Move(0.5, 0.0, 0.0) == 0 and sim.StandDown() == 0
    assert stub_state.read_state(state) == stub_state.StubState("sitting", stub_state.Pose())
    assert sim.Stretch() == sim_backend.STUB_ERR_NOT_STANDING


def test_parse_objects_defaults_and_errors():
    parsed = stub_state.parse_objects({"bottle": {"x": 1, "y": 0}, "car": {"x": 5, "y": 1, "height_m": 1.5}})
    assert parsed == {"bottle": stub_state.WorldObject(1.0, 0.0, 0.25), "car": stub_state.WorldObject(5.0, 1.0, 1.5)}
    with pytest.raises(ValueError, match="'car' needs height_m"):
        stub_state.parse_objects({"car": {"x": 5, "y": 1}})
    bad_objects = (
        {"chair": {"x": 1}},
        {"chair": {"x": 1, "y": 0, "z": 0}},
        {"chair": {"x": "1", "y": 0}},
        {"chair": {"x": math.nan, "y": 0}},
        {"car": {"x": 5, "y": 1, "height_m": 0}},
        [],
    )
    for bad in bad_objects:
        with pytest.raises(ValueError):
            stub_state.parse_objects(bad)


def test_obstacle_ray_cast_follows_the_pose(state, monkeypatch):
    monkeypatch.setenv(STUB_OBJECTS, json.dumps({"chair": {"x": 2.0, "y": 0.0}, "person": {"x": 0.0, "y": 9.0}}))
    near = 2.0 - sim_backend.OBSTACLE_RADIUS_M
    ahead = sim_backend.sample_state().obstacle_m
    assert ahead is not None and ahead.front == pytest.approx(near)  # person beyond the max
    assert ahead == ObstacleDistances(front=ahead.front)
    stub_state.write_pose(stub_state.Pose(0.0, 0.0, math.pi / 2), state)  # after a left turn the chair is on the right
    turned = sim_backend.sample_state().obstacle_m
    assert turned is not None and turned.right == pytest.approx(near)
    assert turned == ObstacleDistances(right=turned.right)


def test_obstacle_inside_a_disc_is_zero_in_every_direction(state, monkeypatch):
    monkeypatch.setenv(STUB_OBJECTS, json.dumps({"chair": {"x": 0.1, "y": 0.0}}))  # centre behind the left/right axis
    assert sim_backend.sample_state().obstacle_m == ObstacleDistances(front=0.0, back=0.0, left=0.0, right=0.0)


# --- state fields and robot faults (services/robot/docs/service.md#fault-injection) ------------------------------


def test_nominal_samples_match_the_robot_and_the_posture_rule(state):
    for posture in stub_state.STUB_POSTURES:
        stub_state.reset(posture, state)
        s = sim_backend.sample_state()
        assert s.posture == posture == real.derive_posture(s.body_height, s.mode)
        assert (s.imu_rpy, s.foot_force, s.gait_type, s.error_code) == ([0.0] * 3, [0.0] * 4, 0, sim_backend.ERROR_CODE)
    assert sim_backend.sample_state().mode == sim_backend.MODE_SITTING
    stub_state.reset("standing", state)
    assert sim_backend.sample_state().mode == sim_backend.MODE_STANDING == 0  # measured on the robot


def test_fallen_tilts_the_robot_and_lasts_until_reset(state):
    reply = run_move(WALK_MPS, 0.0, 0.0, 3.0, publish=False, fault="fallen")
    assert (reply["move_ret"], reply["stop_ret"]) == (0, 0)  # Move is sent without a reply: nothing tells
    s = sim_backend.sample_state()
    assert s.imu_rpy == pytest.approx([sim_backend.FALLEN_ROLL_RAD, 0.0, 0.0]) and s.mode == sim_backend.MODE_FALLEN
    assert s.posture == real.derive_posture(s.body_height, s.mode)
    assert (s.position, s.velocity) == ([0.0, 0.0, sim_backend.FALLEN_BODY_HEIGHT_M], [0.0] * 3)
    sim = sim_backend.SimRobot()
    assert (sim.Move(0.3, 0.0, 0.0), sim.StandDown(), sim.Stretch()) == (0, 0, 0)
    sim.wait(5.0, threading.Event())
    assert stub_state.read_state(state) == stub_state.StubState(
        condition="fallen"
    )  # no command, no motion, no posture change
    stub_state.reset("standing", state)
    assert sim_backend.sample_state().imu_rpy == [0.0] * 3


def test_stuck_holds_the_pose_until_the_sequence_ends(state):
    sim_backend.SimRobot().inject("stuck")
    assert stub_state.read_state(state).condition is None  # nothing to hold without a running command
    reply = run_move(WALK_MPS, 0.0, 0.0, 3.0, publish=False, fault="stuck")
    assert (reply["move_ret"], reply["stop_ret"], reply["duration_s"]) == (0, 0, 3.0)  # the walk reports success
    assert (
        stub_state.read_state(state) == stub_state.StubState()
    )  # still at the origin; the closing StopMove ended stuck
    reply = run_move(WALK_MPS, 0.0, 0.0, 3.0, publish=False)
    assert stub_state.read_state(state).pose.x == pytest.approx(WALK_MPS * (3.0 - sim_backend.LINEAR_DEAD_S))


def test_rejected_fails_every_call_with_the_sdk_code_until_the_next_arm(state):
    reply = run_move(WALK_MPS, 0.0, 0.0, 3.0, publish=False, fault="rejected")
    assert (reply["move_ret"], reply["stop_ret"]) == (sim_backend.SDK_ERR_CLIENT_SEND, 0)  # the stop still goes through
    assert reply["duration_s"] == protocol.CMD_PERIOD_S and stub_state.read_state(state).command is None
    m = service.Motion(sim_backend.SimRobot(), lambda s, a: None, StateStore(lambda msg: msg), sim=True)
    m._fault("rejected")
    assert m.action("StandDown", None)(service.Job(run=lambda j: {}))["ret"] == sim_backend.SDK_ERR_API_TIMEOUT
    assert stub_state.read_posture(state) == "standing"
    m.arm("token", 10.0)
    assert m.action("StandDown", None)(service.Job(run=lambda j: {}))["ret"] == 0
