"""Stub state file, the simulated sport client, odometry and simulated camera (skills/docs/skills.md)."""

from __future__ import annotations

import json
import math
import threading

import pytest

from contract.env import STUB_FAULT, STUB_OBJECTS, STUB_STATE_FILE, STUB_TIME_SCALE
from contract.schema import ObstacleDistances
from skills import real, robot_ipc, robot_service, stub, turn, vision, walk
from skills.robot_ipc import MoveTarget
from skills.state_store import StateStore
from skills.tests.fake_clock import Clock


@pytest.fixture
def state(tmp_path, monkeypatch):
    """A stub with its state file in tmp_path, no time scale, no fault and no running command."""
    path = tmp_path / "logs" / "stub_state.json"
    monkeypatch.setenv(STUB_STATE_FILE, str(path))
    monkeypatch.setenv(STUB_TIME_SCALE, "0")
    monkeypatch.delenv(STUB_FAULT, raising=False)
    return path


def test_reset_overwrites_posture_and_pose_and_rejects_unknown(state):
    stub.write_pose(stub.Pose(1.0, 2.0, 0.5), state)
    stub.reset("sitting", state)
    assert stub.read_state(state) == stub.StubState("sitting", stub.Pose())
    with pytest.raises(ValueError):
        stub.reset("lying", state)  # pyright: ignore[reportArgumentType]
    assert stub.read_posture(state) == "sitting"


def test_writers_merge_and_missing_pose_is_origin(state):
    assert stub.read_state(state) == stub.StubState("standing", stub.Pose())  # no file: standing at the origin
    stub.write_posture("sitting", state)  # creates the missing parent directory
    state.write_text(json.dumps({"posture": "sitting"}))
    assert stub.read_state(state) == stub.StubState("sitting", stub.Pose())
    stub.write_pose(stub.Pose(1.0, -2.0, 0.25), state)
    stub.write_posture("standing", state)
    stub.write_command((0.1, 0.0, 0.0), state)
    pose = {"x": 1.0, "y": -2.0, "yaw": 0.25}
    expected = {"posture": "standing", "pose": pose, "command": [0.1, 0.0, 0.0], "sequence_s": 0.0, "condition": None}
    assert json.loads(state.read_text()) == expected
    state.write_text(json.dumps({"posture": "standing", "pose": {"x": 1.0}}))
    with pytest.raises(ValueError, match="invalid stub state file"):
        stub.read_state(state)


def test_advance_body_frame():
    p = stub.advance(stub.Pose(0.0, 0.0, math.pi / 2), (0.5, 0.0, 0.0), 2.0)  # forward while facing +y
    assert (p.x, p.y) == pytest.approx((0.0, 1.0))
    p = stub.advance(stub.Pose(), (0.0, 0.3, 0.0), 1.0)  # +vy is left
    assert (p.x, p.y) == pytest.approx((0.0, 0.3))
    p = stub.advance(stub.Pose(), (0.0, 0.0, 1.0), 1.5 * math.pi)  # +vyaw is counter-clockwise, wrapped
    assert (p.x, p.y, p.yaw) == pytest.approx((0.0, 0.0, -math.pi / 2))
    p = stub.advance(stub.Pose(), (1.0, 0.0, 1.0), math.pi / 2)  # quarter circle of radius 1
    assert (p.x, p.y, p.yaw) == pytest.approx((1.0, 1.0, math.pi / 2))


def test_move_wait_stop_integrates_and_samples_pose(state):
    sim, abort = stub.SimRobot(), threading.Event()
    assert sim.Move(0.5, 0.0, 0.0) == 0
    sim.wait(2.0 + stub.LINEAR_DEAD_S, abort)  # unscaled seconds drive the pose, after the dead time
    moving = stub.sample_state()
    assert moving.velocity == pytest.approx([0.5, 0.0, 0.0])
    assert stub.SimRobot().StopMove() == 0  # another process's client: the command lives in the state file
    sim.wait(5.0, abort)
    at_rest = stub.sample_state()
    assert at_rest.position == pytest.approx([1.0, 0.0, stub.BODY_HEIGHT_STANDING_M])
    assert (at_rest.velocity, at_rest.yaw_speed, at_rest.imu_rpy) == ([0.0, 0.0, 0.0], 0.0, [0.0, 0.0, 0.0])
    stub.write_pose(stub.Pose(0.0, 0.0, math.pi / 2), state)
    assert sim.Move(0.5, 0.0, 0.0) == 0
    assert stub.sample_state().velocity == pytest.approx([0.0, 0.5, 0.0])  # body-frame command in the world frame


def test_dead_time_per_axis_restarts_only_after_stop_move(state):
    sim, abort = stub.SimRobot(), threading.Event()
    assert sim.Move(0.5, 0.0, 1.0) == 0
    sim.wait(stub.YAW_DEAD_S, abort)
    assert stub.read_state(state).pose == stub.Pose()  # no axis has started
    sim.wait(stub.LINEAR_DEAD_S - stub.YAW_DEAD_S, abort)  # yaw turns in place until the linear dead time ends
    pose = stub.read_state(state).pose
    assert (pose.x, pose.y, pose.yaw) == pytest.approx((0.0, 0.0, stub.LINEAR_DEAD_S - stub.YAW_DEAD_S))
    stub.write_pose(stub.Pose(), state)
    assert sim.Move(0.5, 0.0, 0.0) == 0  # a new command within the sequence keeps its progress
    sim.wait(1.0, abort)
    assert stub.read_state(state).pose.x == pytest.approx(0.5)
    assert sim.StopMove() == 0 and sim.Move(0.5, 0.0, 0.0) == 0  # StopMove ends the sequence
    sim.wait(stub.LINEAR_DEAD_S, abort)
    assert stub.read_state(state).pose.x == pytest.approx(0.5)


def run_move(
    vx: float, vy: float, vyaw: float, duration_s: float, target=None, *, publish: bool, fault: str | None = None
) -> dict:
    """One move primitive on the robot service's loop over SimRobot; ``publish``: a state after every tick, as the
    sim service does, else no state at all."""
    sim, store = stub.SimRobot(), StateStore(lambda msg: msg, clock=Clock())

    def wait(seconds, abort):
        sim.wait(seconds, abort)
        if publish:
            store.put(stub.sample_state())

    if publish:
        store.put(stub.sample_state())
    m = robot_service.Motion(sim, wait, store, sim=True)
    threading.Thread(target=m._loop, daemon=True).start()
    job = robot_service.Job(run=m.move(vx, vy, vyaw, duration_s, target, fault))
    m._jobs.put(job)
    assert job.done.wait(5) and job.reply is not None
    return job.reply


def test_stub_walk_covers_its_distance(state):
    params = {"distance_m": 2.55}
    run_move(walk.VELOCITY_MPS, 0.0, 0.0, walk._motion_s(params), publish=False)
    pose = stub.read_state(state).pose
    assert (pose.x, pose.y, pose.yaw) == pytest.approx((2.55, 0.0, 0.0))


def test_stub_turn_covers_its_angle_open_and_closed_loop(state):
    params, yaw = {"angle_deg": 90.0}, math.radians(90.0)
    target = MoveTarget(yaw_rad=yaw, lead_s=turn.LEAD_S)
    tick_rad = robot_ipc.CMD_PERIOD_S * turn.YAW_RATE_RPS
    run_move(0.0, 0.0, turn.YAW_RATE_RPS, turn._motion_s(params), target, publish=False)  # no yaw: open loop
    assert stub.read_state(state).pose.yaw == pytest.approx(yaw, abs=tick_rad / 2)  # whole periods
    stub.reset("standing", state)
    reply = run_move(0.0, 0.0, turn.YAW_RATE_RPS, turn._motion_s(params), target, publish=True)
    assert reply["duration_s"] < turn._motion_s(params)  # stopped on the measured yaw, before the cap
    assert yaw - robot_service.YAW_STOP_MARGIN_RAD <= reply["measured"]["yaw_rad"] < yaw + tick_rad


def test_move_while_sitting_stores_no_command(state):
    stub.reset("sitting", state)
    sim = stub.SimRobot()
    assert sim.Move(0.5, 0.0, 0.0) == stub.STUB_ERR_NOT_STANDING
    sim.wait(2.0, threading.Event())
    assert stub.read_state(state) == stub.StubState("sitting", stub.Pose())


def test_stand_down_clears_the_command(state):
    sim = stub.SimRobot()
    assert sim.Move(0.5, 0.0, 0.0) == 0 and sim.StandDown() == 0
    assert stub.read_state(state) == stub.StubState("sitting", stub.Pose())
    assert sim.Stretch() == stub.STUB_ERR_NOT_STANDING


BOTTLE_HEIGHT_M = 0.25


def bottle_at(depth: float, left: float = 0.0) -> stub.WorldObject:
    """A ``BOTTLE_HEIGHT_M`` bottle at camera depth ``depth`` and ``left`` metres to the left, seen from the origin."""
    return stub.WorldObject(x=stub.CAMERA_OFFSET_M + depth, y=left, height_m=BOTTLE_HEIGHT_M)


def depth_for(h_frac: float) -> float:
    """The camera depth at which a ``bottle_at`` box is ``h_frac`` of the image height."""
    return stub.CAMERA_FOCAL_PX * BOTTLE_HEIGHT_M / (h_frac * stub.CAMERA_HEIGHT_PX)


def confidence_at(h_frac: float) -> float:
    return stub.CONFIDENCE_MIN + (stub.CONFIDENCE_MAX - stub.CONFIDENCE_MIN) * h_frac


def test_project_closeness_position_and_confidence():
    h_near, h_medium, h_far = (
        (vision.NEAR_MIN_H + 1) / 2,
        (vision.MEDIUM_MIN_H + vision.NEAR_MIN_H) / 2,
        (stub.MIN_BOX_H + vision.MEDIUM_MIN_H) / 2,
    )
    near, medium, far = (stub.project(stub.Pose(), bottle_at(depth_for(h))) for h in (h_near, h_medium, h_far))
    assert (near.position, near.closeness) == ("center", "near")
    assert near.confidence == pytest.approx(confidence_at(h_near), abs=10**-vision.CONFIDENCE_DECIMALS)
    assert (medium.closeness, far.closeness) == ("medium", "far")
    clipped = stub.project(stub.Pose(), bottle_at(depth_for(1.0) / 2))  # box taller than the image: h = 1
    assert (clipped.closeness, clipped.confidence) == ("near", pytest.approx(stub.CONFIDENCE_MAX))
    assert stub.project(stub.Pose(), bottle_at(1.0, left=0.5)).position == "left"
    assert stub.project(stub.Pose(), bottle_at(1.0, left=-0.5)).position == "right"
    facing_left = stub.Pose(0.0, 0.0, math.pi / 2)  # the same bottle, now seen after a left turn
    assert stub.project(facing_left, stub.WorldObject(0.0, 2.3, BOTTLE_HEIGHT_M)).position == "center"


def test_project_not_found():
    too_small = bottle_at(depth_for(stub.MIN_BOX_H) * 1.1)
    for obj in (bottle_at(-1.0), bottle_at(1.0, left=2.0), too_small):  # behind, outside the FOV, too small
        assert not stub.project(stub.Pose(), obj).found


def test_classify_box_thresholds():
    r = vision.classify_box(0.39, 0.61, 0.876)
    assert (r.position, r.closeness, r.confidence) == ("left", "near", 0.88)
    r = vision.classify_box(0.5, 0.3, 0.5)
    assert (r.position, r.closeness) == ("center", "far")
    assert vision.classify_box(0.61, 0.31, 0.5).position == "right"
    edges = (vision.classify_box(x, vision.NEAR_MIN_H, 0.5) for x in (vision.LEFT_MAX_X, vision.RIGHT_MIN_X))
    assert [(r.position, r.closeness) for r in edges] == [("center", "medium")] * 2  # thresholds are strict


def test_parse_objects_defaults_and_errors():
    parsed = stub.parse_objects({"bottle": {"x": 1, "y": 0}, "car": {"x": 5, "y": 1, "height_m": 1.5}})
    assert parsed == {"bottle": stub.WorldObject(1.0, 0.0, 0.25), "car": stub.WorldObject(5.0, 1.0, 1.5)}
    with pytest.raises(ValueError, match="'car' needs height_m"):
        stub.parse_objects({"car": {"x": 5, "y": 1}})
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
            stub.parse_objects(bad)


def test_obstacle_ray_cast_follows_the_pose(state, monkeypatch):
    monkeypatch.setenv(STUB_OBJECTS, json.dumps({"chair": {"x": 2.0, "y": 0.0}, "person": {"x": 0.0, "y": 9.0}}))
    near = 2.0 - stub.OBSTACLE_RADIUS_M
    ahead = stub.sample_state().obstacle_m
    assert ahead is not None and ahead.front == pytest.approx(near)  # person beyond the max
    assert ahead == ObstacleDistances(front=ahead.front)
    stub.write_pose(stub.Pose(0.0, 0.0, math.pi / 2), state)  # after a left turn the chair is on the right
    turned = stub.sample_state().obstacle_m
    assert turned is not None and turned.right == pytest.approx(near)
    assert turned == ObstacleDistances(right=turned.right)


def test_obstacle_inside_a_disc_is_zero_in_every_direction(state, monkeypatch):
    monkeypatch.setenv(STUB_OBJECTS, json.dumps({"chair": {"x": 0.1, "y": 0.0}}))  # centre behind the left/right axis
    assert stub.sample_state().obstacle_m == ObstacleDistances(front=0.0, back=0.0, left=0.0, right=0.0)


# --- state fields and robot faults (skills/docs/skills.md#fault-injection) ------------------------------------


def test_nominal_samples_match_the_robot_and_the_posture_rule(state):
    for posture in stub.STUB_POSTURES:
        stub.reset(posture, state)
        s = stub.sample_state()
        assert s.posture == posture == real.derive_posture(s.body_height, s.mode)
        assert (s.imu_rpy, s.foot_force, s.gait_type, s.error_code) == ([0.0] * 3, [0.0] * 4, 0, stub.ERROR_CODE)
    assert stub.sample_state().mode == stub.MODE_SITTING
    stub.reset("standing", state)
    assert stub.sample_state().mode == stub.MODE_STANDING == 0  # measured on the robot


def test_fallen_tilts_the_robot_and_lasts_until_reset(state):
    reply = run_move(walk.VELOCITY_MPS, 0.0, 0.0, 3.0, publish=False, fault="fallen")
    assert (reply["move_ret"], reply["stop_ret"]) == (0, 0)  # Move is sent without a reply: nothing tells
    s = stub.sample_state()
    assert s.imu_rpy == pytest.approx([stub.FALLEN_ROLL_RAD, 0.0, 0.0]) and s.mode == stub.MODE_FALLEN
    assert s.posture == real.derive_posture(s.body_height, s.mode)
    assert (s.position, s.velocity) == ([0.0, 0.0, stub.FALLEN_BODY_HEIGHT_M], [0.0] * 3)
    sim = stub.SimRobot()
    assert (sim.Move(0.3, 0.0, 0.0), sim.StandDown(), sim.Stretch()) == (0, 0, 0)
    sim.wait(5.0, threading.Event())
    assert stub.read_state(state) == stub.StubState(condition="fallen")  # no command, no motion, no posture change
    stub.reset("standing", state)
    assert stub.sample_state().imu_rpy == [0.0] * 3


def test_stuck_holds_the_pose_until_the_sequence_ends(state):
    stub.SimRobot().inject("stuck")
    assert stub.read_state(state).condition is None  # nothing to hold without a running command
    reply = run_move(walk.VELOCITY_MPS, 0.0, 0.0, 3.0, publish=False, fault="stuck")
    assert (reply["move_ret"], reply["stop_ret"], reply["duration_s"]) == (0, 0, 3.0)  # the walk reports success
    assert stub.read_state(state) == stub.StubState()  # still at the origin; the closing StopMove ended stuck
    reply = run_move(walk.VELOCITY_MPS, 0.0, 0.0, 3.0, publish=False)
    assert stub.read_state(state).pose.x == pytest.approx(walk.VELOCITY_MPS * (3.0 - stub.LINEAR_DEAD_S))


def test_rejected_fails_every_call_with_the_sdk_code_until_the_next_arm(state):
    reply = run_move(walk.VELOCITY_MPS, 0.0, 0.0, 3.0, publish=False, fault="rejected")
    assert (reply["move_ret"], reply["stop_ret"]) == (stub.SDK_ERR_CLIENT_SEND, 0)  # the stop still goes through
    assert reply["duration_s"] == robot_ipc.CMD_PERIOD_S and stub.read_state(state).command is None
    m = robot_service.Motion(stub.SimRobot(), lambda s, a: None, StateStore(lambda msg: msg), sim=True)
    m._fault("rejected")
    assert m.action("StandDown", None)(robot_service.Job(run=lambda j: {}))["ret"] == stub.SDK_ERR_API_TIMEOUT
    assert stub.read_posture(state) == "standing"
    m.arm("token", 10.0)
    assert m.action("StandDown", None)(robot_service.Job(run=lambda j: {}))["ret"] == 0
