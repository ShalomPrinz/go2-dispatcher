"""submit_plan tool schema (§12.2, §19.2)."""

from __future__ import annotations

import json

from go2_dispatcher.llm import STEP_REQUIRED, TOOL_REQUIRED, plan_tool_schema
from go2_dispatcher.models import Plan, PlanStep


def test_property_names_equal_model_fields():
    s = plan_tool_schema(5)["input_schema"]
    assert set(s["properties"]) == set(Plan.model_fields)
    assert set(s["properties"]["steps"]["items"]["properties"]) == set(PlanStep.model_fields)


def test_required_equals_constants():
    s = plan_tool_schema(5)["input_schema"]
    assert s["required"] == TOOL_REQUIRED == ["status", "steps"]
    assert s["properties"]["steps"]["items"]["required"] == STEP_REQUIRED == ["skill", "params"]


def test_max_items_is_horizon():
    for h in (1, 3, 7):
        assert plan_tool_schema(h)["input_schema"]["properties"]["steps"]["maxItems"] == h


def test_no_strict_key():
    assert '"strict"' not in json.dumps(plan_tool_schema(5))


def test_name_and_closed_objects():
    t = plan_tool_schema(5)
    assert t["name"] == "submit_plan"
    assert t["input_schema"]["additionalProperties"] is False
    assert t["input_schema"]["properties"]["steps"]["items"]["additionalProperties"] is False
    assert t["input_schema"]["properties"]["status"]["enum"] == ["PLAN", "DONE", "ABORT"]
