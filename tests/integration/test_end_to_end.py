"""End-to-end: ScriptedPlanner + real Executor + stub backend (tests/docs/testing.md)."""

from __future__ import annotations

import json
import os
import signal
import threading
import time
from pathlib import Path

import pytest

from dispatcher.context import PromptSurface
from dispatcher.dispatcher import Dispatcher
from dispatcher.executor import Executor
from dispatcher.models import Plan, PlanStep
from dispatcher.registry import Registry
from dispatcher.runlog import INDEX_FILE, stream_path
from dispatcher.tests.helpers import ScriptedPlanner, make_config
from skills import stub
from tests.helpers import REPO_ROOT

pytestmark = pytest.mark.integration

TURN = PlanStep(skill="turn", params={"direction": "left", "angle_deg": 90})
DETECT = PlanStep(skill="detect_object", params={"target": "chair"})
WALK = PlanStep(skill="walk", params={"direction": "forward", "distance_m": 1.0})
STOP_AFTER_S = 0.5
STOP_WITHIN_S = 3.0


@pytest.fixture(scope="module")
def registry():
    return Registry.load(REPO_ROOT / "skills" / "catalog")


def build(tmp_path, registry, items, **cfg_over):
    cfg = make_config(tmp_path, **cfg_over)
    stub.reset(cfg.stub.initial_posture, cfg.stub.state_file)
    surface = PromptSurface.build(registry, cfg.loop.planning_horizon)
    planner = ScriptedPlanner(items, surface)
    executor = Executor(cfg, cfg.base_dir)
    d = Dispatcher(cfg, registry, planner, executor)  # the first fresh read starts the robot service
    return d, planner, cfg


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def test_turn_then_detect(tmp_path, registry):
    d, _, cfg = build(
        tmp_path,
        registry,
        [Plan(status="PLAN", steps=[TURN, DETECT]), Plan(status="DONE", message="I see a chair.")],
        stub={"objects": {"chair": {"x": 0.0, "y": 2.0}}},  # left of the start pose, seen after the turn
    )
    try:
        o = d.run_task("turn left then look for a chair", source="test")  # the executor starts the vision service
    finally:
        d.shutdown(0)
    assert o.outcome == "DONE", o.message
    assert [s.outcome for s in o.steps] == ["ok", "ok"]
    assert o.steps[1].response is not None
    assert o.steps[1].response.observations["object_found"] is True

    recs = read_jsonl(o.log_path)
    types = [r["type"] for r in recs]
    for t in ("task_start", "llm_request", "llm_response", "plan", "task_end"):
        assert t in types
    assert types.count("step_start") == 2 and types.count("step_result") == 2
    starts = [r for r in recs if r["type"] == "step_start"]
    results = [r for r in recs if r["type"] == "step_result"]
    assert [r["dispatch"]["index"] for r in results] == [r["dispatch"]["index"] for r in starts] == [1, 2]
    assert types[0] == "task_start" and types[-1] == "task_end"
    assert all(r["usage"] for r in recs if r["type"] == "llm_response")
    seqs = [r["seq"] for r in recs]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs) and seqs[0] == 0

    index = read_jsonl(cfg.run_log_dir / INDEX_FILE)
    assert len(index) == 1 and index[0]["run_id"] == o.run_id
    assert index[0]["input_tokens"] == 200
    assert index[0]["model"] == cfg.llm.model and index[0]["thinking"] == cfg.llm.thinking


def test_fault_at_step_two(tmp_path, registry):
    d, planner, _ = build(
        tmp_path,
        registry,
        [Plan(status="PLAN", steps=[TURN, TURN]), Plan(status="DONE", message="done anyway")],
        stub={"faults": [{"step": 2, "kind": "error"}]},
    )
    try:
        o = d.run_task("turn twice", source="test")
    finally:
        d.shutdown(0)
    assert o.outcome == "DONE"
    assert [s.outcome for s in o.steps] == ["ok", "error"]
    assert o.steps[1].dispatch is not None
    assert o.steps[1].dispatch.fault == "error"
    assert o.failures == 1
    assert "Your previous plan failed at step 2 (turn): error." in planner.calls[1]["user"]


def test_stop_during_hang(tmp_path, registry):
    d, _, _ = build(
        tmp_path, registry, [Plan(status="PLAN", steps=[TURN])], stub={"faults": [{"step": 1, "kind": "hang"}]}
    )
    timer = threading.Timer(STOP_AFTER_S, d.request_stop, args=("test",))
    t0 = time.monotonic()
    timer.start()
    try:
        o = d.run_task("turn", source="test")
    finally:
        timer.cancel()
        d.shutdown(0)
    assert time.monotonic() - t0 < STOP_WITHIN_S
    assert o.outcome == "STOPPED"
    sm = [r for r in read_jsonl(o.log_path) if r["type"] == "stop_move"]
    assert sm and all(r["ok"] is True for r in sm)
    assert any(r["type"] == "stop_requested" for r in read_jsonl(o.log_path))


def _service(d):
    assert isinstance(d.executor, Executor)
    return d.executor.robot


def _kill_service(d):
    pid = _service(d).pid
    assert pid is not None
    os.kill(pid, signal.SIGKILL)
    deadline = time.monotonic() + STOP_WITHIN_S
    while _service(d).pid is not None and time.monotonic() < deadline:
        time.sleep(0.01)


def crash_faults(*steps):
    return {"faults": [{"step": n, "kind": "service_crash"} for n in steps]}


def test_service_crash_mid_walk_ends_the_task_and_the_next_task_restarts_it(tmp_path, registry):
    items = [Plan(status="PLAN", steps=[TURN, WALK, TURN]), Plan(status="PLAN", steps=[TURN]), Plan(status="DONE")]
    d, planner, cfg = build(tmp_path, registry, items, stub=crash_faults(2))  # per task: the next task has no step 2
    try:
        o = d.run_task("turn and walk", source="test")
        assert o.outcome == "ROBOT_SERVICE_LOST", o.message
        assert "the fallback stop was confirmed" in o.message and not o.stop_move_failed
        assert o.llm_calls == 1 and len(planner.calls) == 1  # no further LLM call
        assert [s.outcome for s in o.steps] == ["ok", "error"]  # the last turn never ran
        recs = read_jsonl(o.log_path)
        stops = [r for r in recs if r["type"] == "stop_move"]
        assert len(stops) == 1 and stops[0]["ok"] and stops[0]["stop_path"] == "fallback"
        assert stops[0]["reason"] == "robot_service_lost"
        assert stub.read_state(cfg.stub.state_file).command is None
        assert _service(d).pid is None  # the next task's fresh read starts it

        assert read_jsonl(stream_path(Path(o.log_path)))[-1]["type"] == "stream_error"  # the loss ended the stream too

        next_task = d.run_task("turn", source="test")
        assert next_task.outcome == "DONE", next_task.message
        assert [s.outcome for s in next_task.steps] == ["ok"]
        assert _service(d).pid is not None
    finally:
        d.shutdown(0)


def test_service_gone_before_a_step_ends_the_task_without_a_restart(tmp_path, registry):
    """The step cannot be armed and never starts; the service stays down until the next task."""
    items = [Plan(status="PLAN", steps=[TURN])]
    d, planner, _ = build(tmp_path, registry, items)
    planner.on_call = lambda _call: _kill_service(d)
    try:
        o = d.run_task("turn", source="test")
        assert o.outcome == "ROBOT_SERVICE_LOST" and o.llm_calls == 1, o.message
        assert [(s.outcome, s.error_code, s.pid) for s in o.steps] == [("error", "robot_service_lost", None)]
        assert _service(d).pid is None
    finally:
        d.shutdown(0)


def test_service_lost_between_tasks_restarts_at_the_next_task(tmp_path, registry):
    items = [
        Plan(status="PLAN", steps=[TURN]),
        Plan(status="DONE"),
        Plan(status="PLAN", steps=[TURN]),
        Plan(status="DONE"),
    ]
    d, _, _ = build(tmp_path, registry, items)
    try:
        assert d.run_task("turn", source="test").outcome == "DONE"  # opens the control connection
        _kill_service(d)
        o = d.run_task("turn", source="test")
        assert o.outcome == "DONE", o.message
        assert [s.outcome for s in o.steps] == ["ok"]
    finally:
        d.shutdown(0)
