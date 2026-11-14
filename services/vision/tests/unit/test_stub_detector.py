"""The stub camera's projection and the box classification (services/vision/docs/vision.md)."""

from __future__ import annotations

import math

import pytest

from services.robot import stub_state
from services.vision import boxes
from services.vision.detectors import stub

BOTTLE_HEIGHT_M = 0.25


def bottle_at(depth: float, left: float = 0.0) -> stub_state.WorldObject:
    """A ``BOTTLE_HEIGHT_M`` bottle at camera depth ``depth`` and ``left`` metres to the left, seen from the origin."""
    return stub_state.WorldObject(x=stub.CAMERA_OFFSET_M + depth, y=left, height_m=BOTTLE_HEIGHT_M)


def depth_for(h_frac: float) -> float:
    """The camera depth at which a ``bottle_at`` box is ``h_frac`` of the image height."""
    return stub.CAMERA_FOCAL_PX * BOTTLE_HEIGHT_M / (h_frac * stub.CAMERA_HEIGHT_PX)


def confidence_at(h_frac: float) -> float:
    return stub.CONFIDENCE_MIN + (stub.CONFIDENCE_MAX - stub.CONFIDENCE_MIN) * h_frac


def test_project_closeness_position_and_confidence():
    h_near, h_medium, h_far = (
        (boxes.NEAR_MIN_H + 1) / 2,
        (boxes.MEDIUM_MIN_H + boxes.NEAR_MIN_H) / 2,
        (stub.MIN_BOX_H + boxes.MEDIUM_MIN_H) / 2,
    )
    near, medium, far = (stub.project(stub_state.Pose(), bottle_at(depth_for(h))) for h in (h_near, h_medium, h_far))
    assert (near.position, near.closeness) == ("center", "near")
    assert near.confidence == pytest.approx(confidence_at(h_near), abs=10**-boxes.CONFIDENCE_DECIMALS)
    assert (medium.closeness, far.closeness) == ("medium", "far")
    clipped = stub.project(stub_state.Pose(), bottle_at(depth_for(1.0) / 2))  # box taller than the image: h = 1
    assert (clipped.closeness, clipped.confidence) == ("near", pytest.approx(stub.CONFIDENCE_MAX))
    assert stub.project(stub_state.Pose(), bottle_at(1.0, left=0.5)).position == "left"
    assert stub.project(stub_state.Pose(), bottle_at(1.0, left=-0.5)).position == "right"
    facing_left = stub_state.Pose(0.0, 0.0, math.pi / 2)  # the same bottle, now seen after a left turn
    assert stub.project(facing_left, stub_state.WorldObject(0.0, 2.3, BOTTLE_HEIGHT_M)).position == "center"


def test_project_not_found():
    too_small = bottle_at(depth_for(stub.MIN_BOX_H) * 1.1)
    for obj in (bottle_at(-1.0), bottle_at(1.0, left=2.0), too_small):  # behind, outside the FOV, too small
        assert not stub.project(stub_state.Pose(), obj).found


def test_classify_box_thresholds():
    r = boxes.classify_box(0.39, 0.61, 0.876)
    assert (r.position, r.closeness, r.confidence) == ("left", "near", 0.88)
    r = boxes.classify_box(0.5, 0.3, 0.5)
    assert (r.position, r.closeness) == ("center", "far")
    assert boxes.classify_box(0.61, 0.31, 0.5).position == "right"
    edges = (boxes.classify_box(x, boxes.NEAR_MIN_H, 0.5) for x in (boxes.LEFT_MAX_X, boxes.RIGHT_MIN_X))
    assert [(r.position, r.closeness) for r in edges] == [("center", "medium")] * 2  # thresholds are strict
