"""Stub state file, odometry and simulated camera (skills/docs/skills.md)."""

from __future__ import annotations

import json
import math

import pytest

from skills import stub, vision
from skills.env import STUB_FAULT, STUB_STATE_FILE, STUB_TIME_SCALE


@pytest.fixture
def state(tmp_path, monkeypatch):
    """A stub with its state file in tmp_path, no time scale, no fault and no running command."""
    path = tmp_path / "logs" / "stub_state.json"
    monkeypatch.setenv(STUB_STATE_FILE, str(path))
    monkeypatch.setenv(STUB_TIME_SCALE, "0")
    monkeypatch.delenv(STUB_FAULT, raising=False)
    monkeypatch.setattr(stub, "_command", None)
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
    assert json.loads(state.read_text()) == {"posture": "standing", "pose": {"x": 1.0, "y": -2.0, "yaw": 0.25}}
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


def test_move_sleep_stop_integrates_and_samples_pose(state):
    client = stub.get_sport_client()
    assert client.Move(0.5, 0.0, 0.0) == 0
    stub.sleep(2.0)  # unscaled seconds drive the pose
    moving = stub.sample_state()
    assert moving.velocity == pytest.approx([0.5, 0.0, 0.0])
    assert client.StopMove() == 0
    stub.sleep(5.0)
    at_rest = stub.sample_state()
    assert at_rest.position == pytest.approx([1.0, 0.0, stub.BODY_HEIGHT_STANDING_M])
    assert (at_rest.velocity, at_rest.yaw_speed, at_rest.imu_rpy) == ([0.0, 0.0, 0.0], 0.0, [0.0, 0.0, 0.0])
    assert (at_rest.mode, at_rest.gait_type, at_rest.foot_force) == (None, None, None)
    stub.write_pose(stub.Pose(0.0, 0.0, math.pi / 2), state)
    assert client.Move(0.5, 0.0, 0.0) == 0
    assert stub.sample_state().velocity == pytest.approx([0.0, 0.5, 0.0])  # body-frame command in the world frame


def test_move_while_sitting_stores_no_command(state):
    stub.reset("sitting", state)
    assert stub.get_sport_client().Move(0.5, 0.0, 0.0) == stub.STUB_ERR_NOT_STANDING
    stub.sleep(2.0)
    assert stub.read_state(state).pose == stub.Pose()


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
