"""Skill policies: constants as class attributes, formulas, motion costs (docs/skills.md)."""

from __future__ import annotations

import math

import pytest

from go2_skills import detect_object, sit, stretch, turn, walk
from go2_skills.policy_base import MotionCost


def test_names_match_modules():
    for mod in (walk, turn, sit, stretch, detect_object):
        assert mod.POLICY.name == mod.SKILL


def test_walk_policy():
    p = {"direction": "forward", "distance_m": 1.5}
    assert walk.POLICY.timeout_s(p) == pytest.approx(10.0 + 1.5 * 1.5 / 0.3)
    assert walk.POLICY.motion_cost(p) == MotionCost(distance_m=1.5)


def test_turn_policy():
    p = {"direction": "left", "angle_deg": 90}
    assert turn.POLICY.timeout_s(p) == pytest.approx(10.0 + 1.5 * math.radians(90) / 1.0)
    assert turn.POLICY.motion_cost(p) == MotionCost(rotation_deg=90)


def test_fixed_policies():
    assert sit.POLICY.timeout_s({}) == 15.0 and sit.SitPolicy.SETTLE_S == 3.0
    assert stretch.POLICY.timeout_s({}) == 20.0 and stretch.StretchPolicy.SETTLE_S == 6.0
    assert detect_object.POLICY.timeout_s({"target": "chair"}) == 45.0
    for mod in (sit, stretch, detect_object):
        assert mod.POLICY.motion_cost({}) == MotionCost()
    assert detect_object.POLICY.context_observations == (
        "object_found", "position", "closeness", "confidence")


def test_constants_can_be_monkeypatched(monkeypatch):
    monkeypatch.setattr(walk.WalkPolicy, "BASE_S", 0.0)
    monkeypatch.setattr(walk.WalkPolicy, "FACTOR", 0.5)
    assert walk.POLICY.timeout_s({"distance_m": 3.0}) == pytest.approx(5.0)
