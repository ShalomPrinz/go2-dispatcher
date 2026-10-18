"""TeeLog observer seam and the CLI console trace (dispatcher/docs/run-log.md, docs/running.md)."""

from __future__ import annotations

import io

import pytest

from dispatcher.context import PromptSurface
from dispatcher.dispatcher import Dispatcher
from dispatcher.llm import LLMResult
from dispatcher.models import Plan, PlanStep
from dispatcher.registry import Registry
from dispatcher.runlog import NullLog, TeeLog
from dispatcher.tests.helpers import FakeClock, FakeExecutor, ScriptedPlanner, exec_result, make_config
from dispatcher.transports.console_trace import ConsoleTrace
from tests.helpers import REPO_ROOT


@pytest.fixture(scope="module")
def registry():
    return Registry.load(REPO_ROOT / "skills" / "catalog")


class Recorder(NullLog):
    def __init__(self):
        self.errors: list[str] = []

    def llm_error(self, call_index: int, detail: str) -> None:
        self.errors.append(detail)


class Broken(NullLog):
    def llm_error(self, call_index: int, detail: str) -> None:
        raise RuntimeError("observer bug")


def test_tee_forwards_to_log_and_observer_and_swallows_observer_errors():
    log, seen = Recorder(), Recorder()
    TeeLog(log, seen).llm_error(1, "boom")
    assert log.errors == seen.errors == ["boom"]

    log = Recorder()
    TeeLog(log, Broken()).llm_error(1, "boom")  # must not raise
    assert log.errors == ["boom"]


def test_thinking_then_thought_for(registry):
    out, clock = io.StringIO(), FakeClock()
    trace = ConsoleTrace(registry, out, ansi=False, clock=clock.now)
    trace.llm_request(1, "initial", None, "user text")
    clock.advance(5.3)
    res = LLMResult(
        tool_input=None,
        usage={"input_tokens": 120, "output_tokens": 30},
        stop_reason="tool_use",
        content=[],
        latency_ms=5300.0,
        total_ms=5300.0,
        attempts=1,
        response_id=None,
        request_id=None,
    )
    trace.llm_response(1, res)
    assert out.getvalue() == "Thinking...\nThought for 5.3s · 120 in / 30 out tokens\n"


def test_dispatcher_feeds_the_trace(tmp_path, registry):
    cfg = make_config(tmp_path)
    surface = PromptSurface.build(registry, cfg.loop.planning_horizon)
    walk = PlanStep(skill="walk", params={"direction": "forward", "distance_m": 0.5})
    planner = ScriptedPlanner([Plan(status="PLAN", steps=[walk]), Plan(status="DONE", message="ok")], surface)
    out = io.StringIO()
    d = Dispatcher(
        cfg,
        registry,
        planner,
        FakeExecutor([exec_result()]),
        clock=FakeClock().now,
        observer=ConsoleTrace(registry, out, ansi=False),
    )
    outcome = d.run_task("walk", source="test")
    lines = out.getvalue().splitlines()
    assert sum(ln.startswith("Thinking...") for ln in lines) == 2
    assert "▶ walk(direction=forward, distance_m=0.5)" in lines
    assert any(ln.startswith("✓ walk(") for ln in lines)
    assert lines[-2] == "Plan: DONE: ok"  # the message is not repeated
    assert lines[-1].startswith("DONE · 2 LLM calls · 1 steps run, 0 failed")
    assert outcome.log_path  # the run log is still written


def test_observer_hears_stop_only_after_the_kill(tmp_path, registry):
    cfg = make_config(tmp_path)
    surface = PromptSurface.build(registry, cfg.loop.planning_horizon)
    walk = PlanStep(skill="walk", params={"direction": "forward", "distance_m": 0.5})
    planner = ScriptedPlanner([Plan(status="PLAN", steps=[walk])], surface)
    kills_seen: list[int] = []

    class Observer(NullLog):
        def stop_requested(self, source: str, during: str) -> None:
            kills_seen.append(len(executor.kills))

    def step(call):
        d.request_stop("test")
        return exec_result()

    executor = FakeExecutor([step])
    d = Dispatcher(cfg, registry, planner, executor, clock=FakeClock().now, observer=Observer())
    assert d.run_task("walk", source="test").outcome == "STOPPED"
    assert kills_seen == [1]


def test_message_not_on_a_plan_line_is_printed_before_the_summary(tmp_path, registry):
    cfg = make_config(tmp_path)
    surface = PromptSurface.build(registry, cfg.loop.planning_horizon)
    walk = PlanStep(skill="walk", params={"direction": "forward", "distance_m": 0.5})
    planner = ScriptedPlanner([Plan(status="PLAN", steps=[walk])], surface)

    def step(call):
        d.request_stop("test")
        return exec_result()

    out = io.StringIO()
    d = Dispatcher(
        cfg,
        registry,
        planner,
        FakeExecutor([step]),
        clock=FakeClock().now,
        observer=ConsoleTrace(registry, out, ansi=False),
    )
    outcome = d.run_task("walk", source="test")
    lines = out.getvalue().splitlines()
    assert outcome.outcome == "STOPPED" and outcome.message
    assert lines[-2] == outcome.message
    assert lines[-1].startswith("STOPPED · ")
