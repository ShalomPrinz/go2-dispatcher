"""Plan-level validation, one test per rule (§13.1, §19.2)."""

from __future__ import annotations

from go2_dispatcher.models import Plan, PlanStep
from go2_dispatcher.validation import validate_tool_input

H = 5
STEP = {"skill": "walk", "params": {"distance_m": 1.0}}


def v(raw, horizon=H):
    return validate_tool_input(raw, horizon)


def test_valid_plan():
    plan, errors, hx, kind = v({"status": "PLAN", "steps": [STEP, STEP], "replan_after": 1})
    assert errors == [] and not hx and kind == "none"
    assert isinstance(plan, Plan) and plan.replan_after == 1 and len(plan.steps) == 2


def test_lowercase_status_accepted():
    plan, errors, _, kind = v({"status": "done", "steps": [], "message": "ok"})
    assert errors == [] and kind == "none" and plan.status == "DONE"


def test_replan_after_string_rejected_strict():
    plan, errors, _, kind = v({"status": "PLAN", "steps": [STEP, STEP], "replan_after": "2"})
    assert plan is None and kind == "schema"
    assert len(errors) == 1 and errors[0].startswith("replan_after: ")


def test_plan_with_zero_steps():
    plan, errors, _, kind = v({"status": "PLAN", "steps": []})
    assert plan is None and kind == "semantic"
    assert errors == ["status PLAN needs at least one step"]


def test_done_with_steps():
    plan, errors, _, kind = v({"status": "DONE", "steps": [STEP], "message": "x"})
    assert plan is None and kind == "semantic"
    assert errors == ["status DONE must have no steps"]


def test_done_without_message():
    _, errors, _, kind = v({"status": "DONE", "steps": []})
    assert errors == ["status DONE needs a message"] and kind == "semantic"


def test_abort_with_blank_message():
    _, errors, _, kind = v({"status": "ABORT", "steps": [], "message": "   "})
    assert errors == ["status ABORT needs a message"] and kind == "semantic"


def test_replan_after_zero():
    _, errors, _, kind = v({"status": "PLAN", "steps": [STEP, STEP], "replan_after": 0})
    assert errors == ["replan_after must be between 1 and 2"] and kind == "semantic"


def test_replan_after_beyond_steps():
    _, errors, _, _ = v({"status": "PLAN", "steps": [STEP, STEP], "replan_after": 3})
    assert errors == ["replan_after must be between 1 and 2"]


def test_replan_after_with_done():
    _, errors, _, kind = v({"status": "DONE", "steps": [], "message": "m", "replan_after": 1})
    assert errors == ["replan_after is only allowed with status PLAN"] and kind == "semantic"


def test_too_many_steps():
    plan, errors, hx, kind = v({"status": "PLAN", "steps": [STEP] * (H + 1)})
    assert plan is None and hx is True and kind == "horizon"
    assert errors == [f"plan has {H + 1} steps; the maximum is {H}"]


def test_too_many_steps_and_schema_error():
    plan, errors, hx, kind = v({"status": "MAYBE", "steps": [STEP] * (H + 2)})
    assert plan is None and hx is True and kind == "horizon"
    assert errors[0] == f"plan has {H + 2} steps; the maximum is {H}"
    assert any(e.startswith("status: ") for e in errors[1:])


def test_exactly_horizon_steps_ok():
    plan, errors, hx, _ = v({"status": "PLAN", "steps": [STEP] * H})
    assert plan is not None and errors == [] and not hx


def test_extra_top_level_key():
    plan, errors, _, kind = v({"status": "PLAN", "steps": [STEP], "why": "because"})
    assert plan is None and kind == "schema"
    assert errors == ["why: Extra inputs are not permitted"]


def test_missing_params_defaults_to_empty():
    plan, errors, _, _ = v({"status": "PLAN", "steps": [{"skill": "sit"}]})
    assert errors == [] and plan.steps == [PlanStep(skill="sit", params={})]


def test_nested_error_dotted_loc():
    _, errors, _, kind = v({"status": "PLAN", "steps": [STEP, {"skill": 3, "params": {}}]})
    assert kind == "schema" and errors[0].startswith("steps.1.skill: ")


def test_non_dict_input():
    plan, errors, hx, kind = v("not a plan")
    assert plan is None and not hx and kind == "schema"
    assert len(errors) == 1 and errors[0].startswith("input: ")


def test_plans_never_truncated():
    raw = {"status": "PLAN", "steps": [STEP] * (H + 1)}
    validate_tool_input(raw, H)
    assert len(raw["steps"]) == H + 1
