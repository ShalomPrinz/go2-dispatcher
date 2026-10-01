"""Step bounds, whole-plan pre-check, motion budget (dispatcher/docs/loop-and-context.md, docs/testing.md)."""

from __future__ import annotations

import math

import pytest

from dispatcher.bounds import ERROR_MESSAGE_MAX, check_step, cut_message, precheck
from dispatcher.budget import MotionBudget
from dispatcher.models import Plan, PlanStep
from dispatcher.policies import MotionCost, SkillPolicy
from dispatcher.registry import ParamSpec, Registry, SkillDescriptor

from tests.helpers import REPO_ROOT


@pytest.fixture(scope="module")
def registry() -> Registry:
    return Registry.load(REPO_ROOT / "skills" / "catalog")


class _NullPolicy(SkillPolicy):
    name = "count"

    def timeout_s(self, p):
        return 1.0


@pytest.fixture
def typed_registry() -> Registry:
    """A registry with integer, string and one-sided range params (no real skill has them)."""
    params = {
        "n": ParamSpec(type="integer", description="n", min=1),
        "label": ParamSpec(type="string", description="label", default="x"),
        "level": ParamSpec(type="number", description="level", max=5, default=1.0),
    }
    return Registry({"count": SkillDescriptor(name="count", entrypoint="x", description="d",
                                              params=params, policy=_NullPolicy())})


def step(skill, **params):
    return PlanStep(skill=skill, params=params)


# --- bounds ------------------------------------------------------------------------


def test_unknown_skill_lists_names(registry):
    filled, v = check_step(step("fly", height=2), registry)
    assert filled is None
    assert v == ["unknown skill 'fly'; available: detect_object, sit, stretch, turn, walk"]


def test_unknown_param(registry):
    filled, v = check_step(step("sit", speed=1), registry)
    assert filled is None
    assert v == ["unknown parameter 'speed' for skill sit"]


def test_missing_required(registry):
    filled, v = check_step(step("walk", distance_m=1.0), registry)
    assert filled is None
    assert v == ["missing parameter 'direction' for skill walk"]


def test_string_for_number(registry):
    _, v = check_step(step("walk", direction="forward", distance_m="1"), registry)
    assert v == ["parameter 'distance_m' for skill walk must be a finite number, got '1'"]


def test_bool_for_number(registry):
    _, v = check_step(step("walk", direction="forward", distance_m=True), registry)
    assert v == ["parameter 'distance_m' for skill walk must be a finite number, got True"]


def test_non_finite_number(registry):
    _, v = check_step(step("walk", direction="forward", distance_m=math.inf), registry)
    assert len(v) == 1 and "must be a finite number" in v[0]


def test_huge_integer_is_violation_not_exception(registry, typed_registry):
    huge = 10**400
    _, v = check_step(step("walk", direction="forward", distance_m=huge), registry)
    assert v == [f"parameter 'distance_m' for skill walk must be a finite number, "
                 f"got {repr(huge)[:39]}…"]
    _, v = check_step(step("count", n=-huge), typed_registry)
    assert len(v) == 1 and v[0].startswith("parameter 'n' for skill count must be a finite number")
    plan = make_plan(step("walk", direction="forward", distance_m=huge))
    res = precheck(plan, 1, registry, MotionBudget(10, 720), call_index=1)
    assert res.rejection is not None and res.rejection.error_code == "bounds"


def test_integer_accepts_integral_float_and_converts(typed_registry):
    filled, v = check_step(step("count", n=2.0), typed_registry)
    assert v == []
    assert filled["n"] == 2 and type(filled["n"]) is int


def test_integer_rejects_fraction_and_bool(typed_registry):
    _, v = check_step(step("count", n=2.5), typed_registry)
    assert v == ["parameter 'n' for skill count must be an integer, got 2.5"]
    _, v = check_step(step("count", n=False), typed_registry)
    assert v == ["parameter 'n' for skill count must be an integer, got False"]


def test_empty_string(typed_registry):
    _, v = check_step(step("count", n=1, label="   "), typed_registry)
    assert v == ["parameter 'label' for skill count must be a non-empty string, got '   '"]


def test_string_stripped(typed_registry):
    filled, _ = check_step(step("count", n=1, label="  hi "), typed_registry)
    assert filled["label"] == "hi"


def test_enum_normalised(registry):
    filled, v = check_step(step("walk", direction=" Forward ", distance_m=1), registry)
    assert v == []
    assert filled == {"direction": "forward", "distance_m": 1}


def test_enum_invalid(registry):
    _, v = check_step(step("turn", direction="up"), registry)
    assert v == ["parameter 'direction' for skill turn must be one of left, right, got 'up'"]


def test_out_of_range_both_sides(registry):
    _, v = check_step(step("walk", direction="forward", distance_m=0.05), registry)
    assert v == ["parameter 'distance_m' for skill walk is 0.05, outside 0.1 to 3"]
    _, v = check_step(step("walk", direction="forward", distance_m=3.5), registry)
    assert v == ["parameter 'distance_m' for skill walk is 3.5, outside 0.1 to 3"]


def test_one_sided_range(typed_registry):
    _, v = check_step(step("count", n=0), typed_registry)
    assert v == ["parameter 'n' for skill count is 0, below the minimum 1"]
    _, v = check_step(step("count", n=1, level=6), typed_registry)
    assert v == ["parameter 'level' for skill count is 6, above the maximum 5"]


def test_defaults_filled(registry):
    filled, v = check_step(step("walk", direction="left"), registry)
    assert v == []
    assert filled == {"direction": "left", "distance_m": 0.9}
    filled, _ = check_step(step("turn", direction="right"), registry)
    assert filled == {"direction": "right", "angle_deg": 45}


def test_number_kept_as_received(registry):
    filled, _ = check_step(step("walk", direction="forward", distance_m=2), registry)
    assert type(filled["distance_m"]) is int


def test_several_violations_collected(registry):
    filled, v = check_step(step("walk", distance_m=9, speed=1), registry)
    assert filled is None
    assert v == [
        "unknown parameter 'speed' for skill walk",
        "missing parameter 'direction' for skill walk",
        "parameter 'distance_m' for skill walk is 9, outside 0.1 to 3",
    ]


# --- precheck ----------------------------------------------------------------------


def make_plan(*steps, replan_after=None):
    return Plan(status="PLAN", steps=list(steps), replan_after=replan_after)


def test_precheck_bounds_violation_last_step(registry):
    raw = {"direction": "FORWARD", "distance_m": 99}
    plan = make_plan(step("sit"), step("walk", direction="forward"), PlanStep(skill="walk", params=raw))
    res = precheck(plan, 3, registry, MotionBudget(10, 720), call_index=2)
    r = res.rejection
    assert res.filled == []
    assert r is not None
    assert (r.index, r.call_index, r.plan_step, r.skill) == (None, 2, 3, "walk")
    assert r.params == raw
    assert (r.outcome, r.error_code) == ("rejected", "bounds")
    assert r.error_message == "parameter 'distance_m' for skill walk is 99, outside 0.1 to 3"
    assert r.duration_ms == 0.0


def test_precheck_rejection_message_cut(registry):
    plan = make_plan(step("sit", **{f"p{i:02d}": 1 for i in range(20)}))
    r = precheck(plan, 1, registry, MotionBudget(10, 720), call_index=1).rejection
    assert len(r.error_message) == ERROR_MESSAGE_MAX
    assert r.error_message.endswith("…")


def test_precheck_motion_budget_crossing(registry):
    budget = MotionBudget(max_distance_m=2, max_rotation_deg=720)
    plan = make_plan(step("walk", direction="forward", distance_m=1.5),
                     step("walk", direction=" Backward", distance_m=1.0))
    res = precheck(plan, 2, registry, budget, call_index=1)
    r = res.rejection
    assert res.filled == []
    assert (r.plan_step, r.outcome, r.error_code) == (2, "motion_budget_exceeded", "motion_budget_exceeded")
    assert r.params == {"direction": "backward", "distance_m": 1.0}
    assert r.error_message == ("this step needs 1 m of travel but only 0.5 m remain for this task")
    assert budget.used_distance_m == 0.0     # simulated on a copy


def test_precheck_starts_from_current_usage(registry):
    budget = MotionBudget(max_distance_m=10, max_rotation_deg=100)
    budget.charge(MotionCost(rotation_deg=60))
    plan = make_plan(step("turn", direction="left", angle_deg=45))
    r = precheck(plan, 1, registry, budget, call_index=1).rejection
    assert r.error_message == "this step needs 45 deg of rotation but only 40 deg remain for this task"
    assert budget.used_rotation_deg == 60


def test_precheck_after_stop_at_bounds_checked_not_budget_checked(registry):
    budget = MotionBudget(max_distance_m=2, max_rotation_deg=720)
    plan = make_plan(step("walk", direction="forward", distance_m=1.5),
                     step("walk", direction="forward", distance_m=1.5), replan_after=1)
    res = precheck(plan, 1, registry, budget, call_index=1)
    assert res.rejection is None
    assert res.filled == [{"direction": "forward", "distance_m": 1.5}] * 2

    bad = make_plan(step("walk", direction="forward", distance_m=1.5),
                    step("walk", direction="sideways"), replan_after=1)
    r = precheck(bad, 1, registry, budget, call_index=1).rejection
    assert (r.plan_step, r.outcome) == (2, "rejected")


def test_precheck_ok_fills_every_step(registry):
    plan = make_plan(step("sit"), step("turn", direction="LEFT"), step("detect_object", target="Cup"))
    res = precheck(plan, 3, registry, MotionBudget(10, 720), call_index=1)
    assert res.rejection is None
    assert res.filled[0] == {}
    assert res.filled[1] == {"direction": "left", "angle_deg": 45}
    assert len(res.filled) == 3


# --- motion budget -----------------------------------------------------------------


def test_budget_accumulates():
    b = MotionBudget(10, 720)
    b.charge(MotionCost(distance_m=1.5))
    b.charge(MotionCost(distance_m=2.0, rotation_deg=90))
    assert b.used_distance_m == pytest.approx(3.5)
    assert b.used_rotation_deg == pytest.approx(90)


def test_budget_travel_and_rotation_independent():
    b = MotionBudget(1, 90)
    b.charge(MotionCost(distance_m=1))
    assert b.would_exceed(MotionCost(rotation_deg=90)) is None
    assert b.would_exceed(MotionCost(distance_m=0.1)) == "travel"
    b2 = MotionBudget(1, 90)
    b2.charge(MotionCost(rotation_deg=90))
    assert b2.would_exceed(MotionCost(distance_m=1)) is None
    assert b2.would_exceed(MotionCost(rotation_deg=1)) == "rotation"


def test_budget_travel_checked_first():
    b = MotionBudget(1, 1)
    assert b.would_exceed(MotionCost(distance_m=2, rotation_deg=2)) == "travel"


def test_budget_boundary_allowed():
    b = MotionBudget(1.0, 90)
    for _ in range(10):
        assert b.would_exceed(MotionCost(distance_m=0.1)) is None   # float sums within 1e-9
        b.charge(MotionCost(distance_m=0.1))
    assert b.would_exceed(MotionCost(distance_m=1e-6)) == "travel"
    assert MotionBudget(1, 90).would_exceed(MotionCost(rotation_deg=90)) is None


def test_budget_copy_independent():
    b = MotionBudget(5, 90)
    b.charge(MotionCost(distance_m=1))
    c = b.copy()
    c.charge(MotionCost(distance_m=2, rotation_deg=10))
    assert (b.used_distance_m, b.used_rotation_deg) == (1, 0)
    assert (c.used_distance_m, c.used_rotation_deg) == (3, 10)
    assert (c.max_distance_m, c.max_rotation_deg) == (5, 90)


def test_budget_message_rounds_to_two_decimals():
    b = MotionBudget(1, 90)
    b.charge(MotionCost(distance_m=0.123456))
    assert b.exceeded_message("travel", MotionCost(distance_m=2.987)) == (
        "this step needs 2.99 m of travel but only 0.88 m remain for this task")


def test_cut_message():
    assert cut_message("a\nb") == "a b"
    assert cut_message("x" * 250) == "x" * 199 + "…"
