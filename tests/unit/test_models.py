"""Data models (docs/architecture.md): Plan parsing, StepResult defaults, outcome models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from go2_dispatcher.models import (
    FAILURE_OUTCOMES,
    MotionCostModel,
    Plan,
    StepResult,
    StopMoveResult,
    TaskOutcome,
    TaskSummary,
)


def test_plan_status_normalised_and_defaults():
    p = Plan.model_validate({"status": " plan ", "steps": [{"skill": "sit"}]})
    assert p.status == "PLAN" and p.steps[0].params == {} and p.replan_after is None


@pytest.mark.parametrize("bad", ["2", 2.0, True])
def test_plan_replan_after_strict(bad):
    with pytest.raises(ValidationError):
        Plan.model_validate({"status": "PLAN", "steps": [], "replan_after": bad})


def test_plan_extra_key_rejected():
    with pytest.raises(ValidationError):
        Plan.model_validate({"status": "DONE", "message": "x", "extra": 1})
    with pytest.raises(ValidationError):
        Plan.model_validate({"status": "PLAN", "steps": [{"skill": "sit", "x": 1}]})


def test_step_result_defaults_and_roundtrip():
    sr = StepResult(index=None, call_index=1, plan_step=2, skill="walk",
                    params={"direction": "up"}, outcome="rejected")
    assert sr.verification == "unverified"
    assert sr.motion_cost == MotionCostModel()
    assert StepResult.model_validate_json(sr.model_dump_json()) == sr
    with pytest.raises(ValidationError):
        StepResult(index=1, call_index=1, plan_step=1, skill="x", params={}, outcome="bogus")


def test_failure_outcomes():
    assert FAILURE_OUTCOMES == {"error", "timeout", "malformed", "rejected", "motion_budget_exceeded"}
    assert "interrupted" not in FAILURE_OUTCOMES and "ok" not in FAILURE_OUTCOMES


def test_outcome_and_summary():
    sm = StopMoveResult(ok=True, reason="operator", duration_ms=12.0)
    sr = StepResult(index=1, call_index=1, plan_step=1, skill="sit", params={},
                    outcome="interrupted", stop_move=sm)
    out = TaskOutcome(run_id="r", task="t", outcome="STOPPED", message="m", steps=[sr],
                      llm_calls=1, failures=0, duration_ms=5.0, final_posture="unknown",
                      log_path="runs/r.jsonl")
    assert out.stop_move_failed is False
    summ = TaskSummary(task="t", outcome=out.outcome, message="m", last_step=sr)
    assert TaskSummary.model_validate_json(summ.model_dump_json()) == summ
    with pytest.raises(ValidationError):
        TaskSummary(task="t", outcome="FINE", message="m", last_step=None)
