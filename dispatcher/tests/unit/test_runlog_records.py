"""Keys and values of the records the integration scenario does not produce, through the real writer
(dispatcher/docs/run-log.md)."""

from __future__ import annotations

import json

import pytest

from dispatcher.context import build_prompt_surface
from dispatcher.dispatcher import Dispatcher
from dispatcher.llm.client import LLMInterrupted, LLMUnavailable
from dispatcher.models import StopCause
from dispatcher.planning.plan import Plan, PlanStep
from dispatcher.registry import Registry
from dispatcher.runlog.writer import RunLog
from dispatcher.tests.helpers import (
    BUILTIN_SKILLS_DIR,
    FakeClock,
    FakeExecutor,
    ScriptedPlanner,
    make_config,
    make_runlog,
)

ENVELOPE_LEN = 6  # ts, t_mono_ms, session_id, run_id, seq, type
WALK = PlanStep(skill="walk", params={"direction": "forward", "distance_m": 0.5})


@pytest.fixture(scope="module")
def registry():
    return Registry.load(BUILTIN_SKILLS_DIR)


def payload(tmp_path, registry, items, results=(), *, type_: str, **cfg) -> list[tuple]:
    """The one ``type_`` record of a task, without the envelope, as ordered (key, value) pairs."""
    config = make_config(tmp_path, **cfg)
    planner = ScriptedPlanner(items, build_prompt_surface(registry, config.loop.planning_horizon))
    executor = FakeExecutor(list(results))
    d = Dispatcher(config, registry, planner, executor, runlog=make_runlog(config, planner), clock=FakeClock().now)
    o = d.run_task("do the thing", source="test")
    with open(o.log_path, encoding="utf-8") as f:
        (rec,) = [r for r in map(json.loads, f) if r["type"] == type_]
    return list(rec.items())[ENVELOPE_LEN:]


def test_llm_error_record(tmp_path, registry):
    got = payload(tmp_path, registry, [LLMUnavailable("APIStatusError 500")], type_="llm_error")
    assert got == [("call_index", 1), ("detail", "APIStatusError 500")]


def test_llm_interrupted_record(tmp_path, registry):
    got = payload(tmp_path, registry, [LLMInterrupted(StopCause.OPERATOR)], type_="llm_interrupted")
    assert got == [("call_index", 1), ("cause", "operator")]


def test_plan_invalid_schema_record(tmp_path, registry):
    bad = {"status": "PLAN", "steps": []}
    got = payload(tmp_path, registry, [bad, Plan(status="DONE", message="ok")], type_="plan_invalid")
    assert got == [
        ("call_index", 1),
        ("tool_input", bad),
        ("rejection_kind", "semantic"),
        ("steps_in_plan", None),
        ("horizon", None),
        ("errors", ["status PLAN needs at least one step"]),
    ]


def test_plan_invalid_horizon_record(tmp_path, registry):
    too_long = {"status": "PLAN", "steps": [WALK.model_dump()] * 3}
    items = [too_long, Plan(status="DONE", message="ok")]
    got = payload(tmp_path, registry, items, type_="plan_invalid", loop={"planning_horizon": 2})
    assert got == [
        ("call_index", 1),
        ("tool_input", too_long),
        ("rejection_kind", "horizon"),
        ("steps_in_plan", 3),
        ("horizon", 2),
        ("errors", ["plan has 3 steps; the maximum is 2"]),
    ]


def test_exception_record(tmp_path, registry):
    got = payload(tmp_path, registry, [Plan(status="PLAN", steps=[WALK])], [RuntimeError("boom")], type_="exception")
    (key, tb) = got.pop()
    assert got == [("where", "run_task"), ("exception_type", "RuntimeError"), ("message", "boom")]
    assert key == "traceback" and tb.startswith("Traceback (most recent call last):\n")
    assert tb.endswith("    raise item\nRuntimeError: boom\n")


def test_writer_raises_on_an_unknown_event(tmp_path):
    session = None  # only task_start reads it
    log = RunLog(tmp_path / "t.jsonl", session=session, session_id="s", run_id="r", t_start_mono=0.0, clock=float)  # type: ignore[arg-type]
    with pytest.raises(AssertionError):
        log.emit(object())  # type: ignore[arg-type]
    log.close()
    assert (tmp_path / "t.jsonl").read_text(encoding="utf-8") == ""
