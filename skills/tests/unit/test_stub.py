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
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"posture": "sitting"}))
    assert stub.read_state(state) == stub.StubState("sitting", stub.Pose())
    stub.write_pose(stub.Pose(1.0, -2.0, 0.25), state)
    stub.write_posture("standing", state)
    assert json.loads(state.read_text()) == {"posture": "standing", "pose": {"x": 1.0, "y": -2.0, "yaw": 0.25}}


def test_advance_body_frame():
    p = stub.advance(stub.Pose(0.0, 0.0, math.pi / 2), (0.5, 0.0, 0.0), 2.0)  # forward while facing +y
    assert (p.x, p.y) == pytest.approx((0.0, 1.0))
    p = stub.advance(stub.Pose(), (0.0, 0.3, 0.0), 1.0)  # +vy is left
    assert (p.x, p.y) == pytest.approx((0.0, 0.3))
    p = stub.advance(stub.Pose(), (0.0, 0.0, 1.0), math.pi)  # +vyaw is counter-clockwise, wrapped
    assert (p.x, p.y, abs(p.yaw)) == pytest.approx((0.0, 0.0, math.pi))
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


def test_move_while_sitting_stores_no_command(state):
    stub.reset("sitting", state)
    assert stub.get_sport_client().Move(0.5, 0.0, 0.0) == stub.STUB_ERR_NOT_STANDING
    stub.sleep(2.0)
    assert stub.read_state(state).pose == stub.Pose()


def bottle_at(depth: float, left: float = 0.0) -> stub.WorldObject:
    """A 0.25 m bottle at camera depth ``depth`` and ``left`` metres to the left, seen from the origin."""
    return stub.WorldObject(x=stub.CAMERA_OFFSET_M + depth, y=left, height_m=0.25)


def test_project_closeness_position_and_confidence():
    near, medium, far = (stub.project(stub.Pose(), bottle_at(d)) for d in (0.2, 0.4, 3.0))
    assert (near.position, near.closeness, near.confidence) == ("center", "near", 0.82)
    assert (medium.closeness, far.closeness) == ("medium", "far")
    assert stub.project(stub.Pose(), bottle_at(1.0, left=0.5)).position == "left"
    assert stub.project(stub.Pose(), bottle_at(1.0, left=-0.5)).position == "right"
    facing_left = stub.Pose(0.0, 0.0, math.pi / 2)  # the same bottle, now seen after a left turn
    assert stub.project(facing_left, stub.WorldObject(0.0, 2.3, 0.25)).position == "center"


def test_project_not_found():
    for obj in (bottle_at(-1.0), bottle_at(1.0, left=2.0), bottle_at(7.0)):  # behind, outside the FOV, too small
        assert not stub.project(stub.Pose(), obj).found


def test_classify_box_thresholds():
    r = vision.classify_box(0.39, 0.61, 0.876)
    assert (r.position, r.closeness, r.confidence) == ("left", "near", 0.88)
    r = vision.classify_box(0.5, 0.3, 0.5)
    assert (r.position, r.closeness) == ("center", "far")
    assert vision.classify_box(0.61, 0.31, 0.5).position == "right"


def test_parse_objects_defaults_and_errors():
    parsed = stub.parse_objects({"bottle": {"x": 1, "y": 0}, "car": {"x": 5, "y": 1, "height_m": 1.5}})
    assert parsed == {"bottle": stub.WorldObject(1.0, 0.0, 0.25), "car": stub.WorldObject(5.0, 1.0, 1.5)}
    with pytest.raises(ValueError, match="'car' needs height_m"):
        stub.parse_objects({"car": {"x": 5, "y": 1}})
    for bad in ({"chair": {"x": 1}}, {"chair": {"x": 1, "y": 0, "z": 0}}, {"chair": {"x": "1", "y": 0}}, []):
        with pytest.raises(ValueError):
            stub.parse_objects(bad)
