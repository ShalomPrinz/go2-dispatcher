"""ScriptedPlanner helper (§19.1)."""

from __future__ import annotations

import threading

import pytest
from helpers import SCRIPTED_USAGE, ScriptedPlanner

from go2_dispatcher.llm import plan_tool_schema
from go2_dispatcher.models import LLMUnavailable, Plan, PlanStep


def call(p, i=1, h=3):
    return p.plan(system=["a", "b"], user="u", tool_schema=plan_tool_schema(h), call_index=i,
                  remaining_s=lambda: 10.0, stop_event=threading.Event(),
                  on_infra_retry=lambda d: None)


def test_items_in_order():
    seen = []
    plan = Plan(status="PLAN", steps=[PlanStep(skill="sit")])
    p = ScriptedPlanner([plan, {"status": "PLAN", "steps": [{"skill": "sit"}] * 4},
                         LLMUnavailable("x")], on_call=seen.append)
    r1 = call(p, 1)
    assert r1.plan == plan and r1.errors == [] and r1.usage == SCRIPTED_USAGE
    r2 = call(p, 2)
    assert r2.plan is None and r2.horizon_exceeded and r2.rejection_kind == "horizon"
    with pytest.raises(LLMUnavailable):
        call(p, 3)
    assert seen == [1, 2, 3]
    assert [c["call_index"] for c in p.calls] == [1, 2, 3] and p.calls[0]["user"] == "u"
    with pytest.raises(AssertionError):
        call(p, 4)
