"""Skill policies: timeouts cover the commanded motion, motion costs (skills/docs/skills.md)."""

from __future__ import annotations

import math

import pytest

from dispatcher.registry import Registry
from skills import detect_object, sit, stretch, turn, walk
from skills.result import MotionCost
from tests.helpers import REPO_ROOT

REGISTRY = Registry.load(REPO_ROOT / "skills" / "catalog")


def max_params(name: str) -> dict:
    """Each param at its registry maximum (enums: first value)."""
    out = {}
    for pname, spec in REGISTRY.get(name).params.items():
        out[pname] = spec.max if spec.max is not None else (spec.values or ("chair",))[0]
    return out


# Commanded motion time plus settle wait, from the skills' own constants.
MOTION_S = {
    "walk": lambda p: p["distance_m"] / walk.VELOCITY_MPS,
    "turn": lambda p: math.radians(p["angle_deg"]) / turn.YAW_RATE_RPS,
    "sit": lambda p: sit.SETTLE_S,
    "stretch": lambda p: stretch.SETTLE_S,
    "detect_object": lambda p: 0.0,
}


def test_policies_cover_catalog():
    assert sorted(MOTION_S) == REGISTRY.names()


@pytest.mark.parametrize("name", sorted(MOTION_S))
def test_timeout_exceeds_motion_at_max_params(name):
    p = max_params(name)
    assert REGISTRY.get(name).policy.timeout_s(p) > MOTION_S[name](p)


def test_motion_costs():
    assert walk.POLICY.motion_cost({"direction": "forward", "distance_m": 1.5}) == MotionCost(distance_m=1.5)
    assert turn.POLICY.motion_cost({"direction": "left", "angle_deg": 90}) == MotionCost(rotation_deg=90)
    for mod in (sit, stretch, detect_object):
        assert mod.POLICY.motion_cost({}) == MotionCost()
    assert detect_object.POLICY.context_observations == ("object_found", "position", "closeness", "confidence")
