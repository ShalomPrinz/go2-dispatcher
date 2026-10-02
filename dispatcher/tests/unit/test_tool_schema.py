"""submit_plan tool schema (dispatcher/docs/loop-and-context.md, tests/docs/testing.md)."""

from __future__ import annotations

from dispatcher.llm import plan_tool_schema
from dispatcher.models import Plan, PlanStep


def test_property_names_equal_model_fields():
    """Drift guard between the hand-written schema and the Plan models."""
    s = plan_tool_schema(5)["input_schema"]
    assert set(s["properties"]) == set(Plan.model_fields)
    assert set(s["properties"]["steps"]["items"]["properties"]) == set(PlanStep.model_fields)
    assert s["required"] == ["status", "steps"]
    assert s["properties"]["steps"]["items"]["required"] == ["skill", "params"]


def test_horizon_closed_objects_and_status_enum():
    for h in (1, 3, 7):
        assert plan_tool_schema(h)["input_schema"]["properties"]["steps"]["maxItems"] == h
    t = plan_tool_schema(5)
    assert t["name"] == "submit_plan"
    assert t["input_schema"]["additionalProperties"] is False
    assert t["input_schema"]["properties"]["steps"]["items"]["additionalProperties"] is False
    assert t["input_schema"]["properties"]["status"]["enum"] == ["PLAN", "DONE", "ABORT"]
