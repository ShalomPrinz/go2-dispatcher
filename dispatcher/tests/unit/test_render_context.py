"""render.py + context.py, with golden files (dispatcher/docs/loop-and-context.md, docs/testing.md)."""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers import REPO_ROOT

from dispatcher.budget import MotionBudget
from dispatcher.context import ContextInput, build_user_message, schema_retry_message
from dispatcher.models import (
    PlanStep,
    RobotState,
    SkillError,
    SkillResponse,
    StepResult,
    TaskSummary,
)
from dispatcher.policies import MotionCost
from dispatcher.registry import Registry
from dispatcher.render import format_value, render_remaining, render_step

GOLDEN = REPO_ROOT / "dispatcher" / "tests" / "golden"
SECTION_ORDER = ["## Previous task", "## Robot", "## Task", "## Budget",
                 "## Executed so far", "## Remaining plan", "## Notice"]
TASK = "Walk forward, then  find the chair"  # verbatim: inner double space kept


@pytest.fixture(scope="module")
def registry() -> Registry:
    return Registry.load(REPO_ROOT / "skills" / "catalog")


def check_golden(name: str, text: str, update: bool) -> None:
    path: Path = GOLDEN / name
    if update:
        path.write_text(text, encoding="utf-8")
    assert text == path.read_text(encoding="utf-8")


def response(skill: str, *, observations=None, error=None, posture=None) -> SkillResponse:
    state = RobotState(t=0.0, backend="stub", posture=posture) if posture else None
    return SkillResponse(
        schema_version=1, skill=skill, status="error" if error else "ok",
        observations=observations or {},
        error=SkillError(code="sdk_error", message=error) if error else None,
        state_after=state)


def ok_step(index, skill, params, *, plan_step=None, call_index=1, observations=None):
    return StepResult(index=index, call_index=call_index, plan_step=plan_step or index,
                      skill=skill, params=params, outcome="ok",
                      response=response(skill, observations=observations))


def make_input(budget=None, **kw) -> ContextInput:
    base = dict(task=TASK, posture="standing", budget=budget or MotionBudget(10.0, 720.0),
                failures=0, max_failures=3, llm_calls=0, max_llm_calls=20, history_k=10,
                return_reason="initial")
    base.update(kw)
    return ContextInput(**base)


def used_budget(distance=0.0, rotation=0.0) -> MotionBudget:
    b = MotionBudget(10.0, 720.0)
    b.charge(MotionCost(distance_m=distance, rotation_deg=rotation))
    return b


# --- render -----------------------------------------------------------------------------


def test_format_value():
    assert format_value("chair") == "chair"
    assert format_value(1.0) == "1"
    assert format_value(0.25) == "0.25"
    assert format_value(3) == "3"
    assert format_value(True) == "true"
    assert format_value(False) == "false"
    assert format_value(None) == "null"
    assert format_value([1, 2]) == "[1, 2]"


def test_params_declared_order_then_extra(registry):
    sr = StepResult(index=None, call_index=1, plan_step=1, skill="walk",
                    params={"speed": 2, "distance_m": 1.5, "direction": "forward"},
                    outcome="rejected", error_message="unknown parameter 'speed' for skill walk")
    assert render_step(sr, registry, numbered=True) == (
        "- rejected before running: walk(direction=forward, distance_m=1.5, speed=2) -> "
        "rejected: unknown parameter 'speed' for skill walk")


def test_unknown_skill_received_order(registry):
    sr = StepResult(index=None, call_index=1, plan_step=2, skill="jump",
                    params={"b": "x", "a": 1}, outcome="rejected", error_message="unknown skill")
    assert "jump(b=x, a=1)" in render_step(sr, registry, numbered=True)


def test_ok_observations_only_context_keys(registry):
    sr = ok_step(4, "detect_object", {"target": "chair"},
                 observations={"target": "chair", "object_found": True, "position": "left",
                               "closeness": "near", "confidence": 0.87, "bbox": [1, 2, 3, 4],
                               "inference_ms": 120.5})
    assert render_step(sr, registry, numbered=True) == (
        "4. detect_object(target=chair) -> ok: object_found=true, position=left, "
        "closeness=near, confidence=0.87")
    walk = ok_step(1, "walk", {"direction": "forward", "distance_m": 1.0},
                   observations={"duration_s": 2.0})
    assert render_step(walk, registry, numbered=True) == "1. walk(direction=forward, distance_m=1) -> ok"
    assert render_step(walk, registry, numbered=False) == "walk(direction=forward, distance_m=1) -> ok"


def test_long_error_cut_to_200(registry):
    msg = "x" * 250
    sr = StepResult(index=1, call_index=1, plan_step=1, skill="sit", params={}, outcome="error",
                    error_message=msg, response=response("sit", error="y"))
    line = render_step(sr, registry, numbered=True)
    prefix = "1. sit() -> error: "
    assert line.startswith(prefix)
    rendered = line[len(prefix):]
    assert len(rendered) == 200 and rendered == "x" * 199 + "…"


def test_render_remaining(registry):
    step = PlanStep(skill="turn", params={"angle_deg": 90, "direction": "left"})
    assert render_remaining(3, step, registry, "pending") == "3. turn(direction=left, angle_deg=90) [pending]"


# --- context (golden) ------------------------------------------------------------------------


def test_first_call(registry, update_golden):
    text = build_user_message(make_input(), registry)
    positions = [text.index(h) for h in SECTION_ORDER]
    assert positions == sorted(positions)
    assert f"## Task\n{TASK}\n\n" in text
    check_golden("context_first_call.txt", text, update_golden)


def test_after_checkpoint(registry, update_golden):
    plan = [PlanStep(skill="walk", params={"direction": "forward", "distance_m": 1.5}),
            PlanStep(skill="detect_object", params={"target": "chair"}),
            PlanStep(skill="turn", params={"direction": "left"})]
    steps = [ok_step(1, "walk", {"direction": "forward", "distance_m": 1.5})]
    inp = make_input(budget=used_budget(distance=1.5), llm_calls=1, steps=steps,
                     remaining=[(2, plan[1]), (3, plan[2])], remaining_tag="pending",
                     return_reason="checkpoint", notice_args={"n": 1})
    text = build_user_message(inp, registry)
    assert "2. detect_object(target=chair) [pending]" in text
    assert "You asked to review results after step 1 of your previous plan." in text
    check_golden("context_checkpoint.txt", text, update_golden)


def test_after_failure(registry, update_golden):
    steps = [
        ok_step(1, "turn", {"direction": "left", "angle_deg": 90}),
        StepResult(index=2, call_index=1, plan_step=2, skill="walk",
                   params={"direction": "forward", "distance_m": 2.0}, outcome="error",
                   error_code="sdk_error", error_message="Move returned 3104",
                   response=response("walk", error="Move returned 3104"),
                   stderr_tail="SECRET traceback"),
    ]
    inp = make_input(budget=used_budget(distance=2.0, rotation=90.0), failures=1, llm_calls=1,
                     steps=steps, remaining=[(3, PlanStep(skill="sit"))],
                     remaining_tag="abandoned", return_reason="failure",
                     notice_args={"n": 2, "skill": "walk", "outcome": "error", "f": 1})
    text = build_user_message(inp, registry)
    assert "3. sit() [abandoned]" in text
    assert "This is failure 1 of 3." in text
    assert "SECRET" not in text
    check_golden("context_failure.txt", text, update_golden)


def test_after_rejection(registry, update_golden):
    plan = [PlanStep(skill="turn", params={"direction": "right"}),
            PlanStep(skill="walk", params={"direction": "up", "distance_m": 9})]
    rejection = StepResult(index=None, call_index=2, plan_step=2, skill="walk",
                           params=dict(plan[1].params), outcome="rejected", error_code="bounds",
                           error_message="parameter 'direction' for skill walk must be one of "
                                         "forward, backward, left, right, got 'up'")
    steps = [ok_step(1, "sit", {}), rejection]
    inp = make_input(budget=used_budget(), failures=1, llm_calls=2, steps=steps,
                     remaining=list(enumerate(plan, start=1)), remaining_tag="abandoned",
                     return_reason="failure",
                     notice_args={"n": 2, "skill": "walk", "outcome": "rejected", "f": 1})
    text = build_user_message(inp, registry)
    assert "- rejected before running: walk(direction=up, distance_m=9) -> rejected: " in text
    assert "1. turn(direction=right) [abandoned]\n2. walk(direction=up, distance_m=9) [abandoned]" in text
    assert "failed at step 2 (walk): rejected" in text
    check_golden("context_rejection.txt", text, update_golden)


def test_truncation_keeps_last_k(registry):
    steps = [ok_step(i, "sit", {}) for i in range(1, 16)]
    inp = make_input(steps=steps, history_k=10, llm_calls=1, return_reason="plan_complete")
    text = build_user_message(inp, registry)
    body = text.split("## Executed so far\n", 1)[1].split("\n\n", 1)[0].splitlines()
    assert len(body) == 1 + 10
    assert body[0] == "(5 earlier entries omitted)"
    assert body[1] == "6. sit() -> ok" and body[-1] == "15. sit() -> ok"


def test_stderr_never_leaks(registry):
    sr = StepResult(index=1, call_index=1, plan_step=1, skill="walk",
                    params={"direction": "forward", "distance_m": 1.0}, outcome="malformed",
                    error_message="no valid response line", stderr_tail="SECRET",
                    exit_code=1)
    prev = TaskSummary(task="t", outcome="FAILURE_BUDGET_EXHAUSTED", message="m", last_step=sr)
    text = build_user_message(make_input(steps=[sr], previous=prev), registry)
    assert "SECRET" not in text


def test_previous_task_and_posture(registry, update_golden):
    last = StepResult(index=2, call_index=1, plan_step=2, skill="sit", params={}, outcome="ok",
                      response=response("sit", posture="sitting"))
    prev = TaskSummary(task="sit down", outcome="DONE", message="I sat down.", last_step=last)
    text = build_user_message(make_input(posture="sitting", previous=prev), registry)
    assert text.startswith("## Previous task\nTask: sit down\nOutcome: DONE\n"
                           "Message: I sat down.\nLast step: sit() -> ok\n\n## Robot\nPosture: sitting\n")
    check_golden("context_previous_task.txt", text, update_golden)

    no_step = TaskSummary(task="hi", outcome="ABORTED", message="Which way?", last_step=None)
    text = build_user_message(make_input(previous=no_step), registry)
    assert "Last step: (none)\n" in text


def test_schema_retry_appends_rejection(registry, update_golden):
    user = build_user_message(make_input(), registry)
    retry = schema_retry_message(user, ["PLAN needs at least one step", "status: bad value"])
    assert retry.startswith(user + "\n\n## Your previous reply was rejected\n")
    assert retry.endswith("PLAN needs at least one step\nstatus: bad value\n"
                          "Call submit_plan again with a corrected plan.")
    check_golden("context_schema_retry.txt", retry, update_golden)
