"""A StepResult dumps its process flat, in the step_result key order (dispatcher/docs/run-log.md)."""

from __future__ import annotations

from contract.policy import MotionCost
from dispatcher.models import ProcessRun, StepDispatch, StepRef, StepResult, TaskSummary

KEYS = [
    "ref",
    "dispatch",
    "outcome",
    "error_code",
    "error_message",
    "response",
    "duration_ms",
    "exit_code",
    "pid",
    "stderr_tail",
    "verification",
    "faults",
]
REF = StepRef(call_index=1, plan_step=1, skill="turn", params={})
DISPATCH = StepDispatch(index=1, timeout_s=10.0, motion_cost=MotionCost())


def test_dispatched_rejected_and_not_started_steps_dump_flat():
    ran = StepResult(
        ref=REF,
        dispatch=DISPATCH,
        outcome="malformed",
        duration_ms=5.0,
        process=ProcessRun(exit_code=139, pid=42, stderr_tail="Segfault"),
    )
    rejected = StepResult(ref=REF, outcome="rejected", error_message="unknown skill")
    not_started = StepResult(ref=REF, dispatch=DISPATCH, outcome="error", error_code="robot_service_lost")
    for step in (ran, rejected, not_started):
        assert list(step.model_dump(mode="json")) == KEYS
        assert list(step.model_dump()) == KEYS
    assert {k: v for k, v in ran.model_dump(mode="json").items() if k in KEYS[5:10]} == {
        "response": None,
        "duration_ms": 5.0,
        "exit_code": 139,
        "pid": 42,
        "stderr_tail": "Segfault",
    }
    for step in (rejected, not_started):
        assert [step.model_dump()[k] for k in ("response", "exit_code", "pid", "stderr_tail")] == [None] * 4
    summary = TaskSummary(task="t", outcome="DONE", message="m", last_step=ran).model_dump(mode="json")
    assert list(summary["last_step"]) == KEYS  # nested dumps flatten too
