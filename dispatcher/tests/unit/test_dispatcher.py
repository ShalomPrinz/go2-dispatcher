"""Dispatcher loop tests with ScriptedPlanner + FakeExecutor + FakeClock (docs/testing.md)."""

from __future__ import annotations

import json
import threading

import pytest

from dispatcher import prompts
from dispatcher.dispatcher import Dispatcher
from dispatcher.executor import STDERR_TAIL_CHARS
from dispatcher.models import BusyError, LLMInterrupted, LLMUnavailable, Plan, PlanStep
from dispatcher.registry import Registry
from dispatcher.runlog import RunLog, RunLogFactory
from dispatcher.tests.helpers import (
    FakeClock,
    FakeExecutor,
    ScriptedPlanner,
    exec_result,
    make_config,
    stop_move_result,
)
from tests.helpers import REPO_ROOT

WAIT_S = 5.0


@pytest.fixture(scope="module")
def registry():
    return Registry.load(REPO_ROOT / "skills" / "catalog")


def walk(d=0.5, direction="forward"):
    return PlanStep(skill="walk", params={"direction": direction, "distance_m": d})


def turn(a=90, direction="left"):
    return PlanStep(skill="turn", params={"direction": direction, "angle_deg": a})


def detect(target="chair"):
    return PlanStep(skill="detect_object", params={"target": target})


def plan(*steps, replan_after=None):
    return Plan(status="PLAN", steps=list(steps), replan_after=replan_after)


def done(message="all done"):
    return Plan(status="DONE", message=message)


class Rig:
    def __init__(self, tmp_path, registry, items, results=(), *, on_call=None,
                 executor=None, posture="standing", **cfg):
        self.cfg = make_config(tmp_path, **cfg)
        self.planner = ScriptedPlanner(items, on_call=on_call)
        self.executor = executor or FakeExecutor(results)
        self.clock = FakeClock()
        self.d = Dispatcher(self.cfg, registry, self.planner, self.executor,
                            RunLogFactory(self.cfg.log.dir, session_id="s1"),
                            clock=self.clock, initial_posture=posture)

    def run(self, task="do the thing"):
        return self.d.run_task(task, source="test")

    def user(self, n):
        return self.planner.calls[n]["user"]


def records(outcome, type_=None):
    with open(outcome.log_path, encoding="utf-8") as f:
        recs = [json.loads(line) for line in f]
    return [r for r in recs if type_ is None or r["type"] == type_]


def reasons(outcome):
    return [r["return_reason"] for r in records(outcome, "llm_request")]


def test_plan_then_done(tmp_path, registry):
    r = Rig(tmp_path, registry, [plan(walk(), turn(), detect()), done()], [exec_result()] * 3)
    o = r.run()
    assert o.outcome == "DONE"
    assert [s.index for s in o.steps] == [1, 2, 3]
    assert o.llm_calls == 2
    assert reasons(o) == ["initial", "plan_complete"]
    assert prompts.NOTICES["plan_complete"] in r.user(1)


def test_immediate_done(tmp_path, registry):
    r = Rig(tmp_path, registry, [done("nothing to do")])
    o = r.run()
    assert (o.outcome, o.message, o.steps, r.executor.runs) == ("DONE", "nothing to do", [], [])


def test_abort_with_question(tmp_path, registry):
    r = Rig(tmp_path, registry, [Plan(status="ABORT", message="Which chair?")])
    o = r.run()
    assert (o.outcome, o.message) == ("ABORTED", "Which chair?")


def test_checkpoint(tmp_path, registry):
    r = Rig(tmp_path, registry,
            [plan(walk(), turn(), detect(), replan_after=1), plan(turn()), done()],
            [exec_result()] * 2)
    o = r.run()
    assert o.outcome == "DONE"
    user = r.user(1)
    assert "2. turn(direction=left, angle_deg=90) [pending]" in user
    assert "3. detect_object(target=chair) [pending]" in user
    assert reasons(o)[1] == "checkpoint"
    assert o.failures == 0


def test_failure_abandons_rest(tmp_path, registry):
    r = Rig(tmp_path, registry, [plan(walk(), turn(), detect()), done()],
            [exec_result(), exec_result("error", skill="turn")])
    o = r.run()
    assert len(r.executor.runs) == 2
    assert o.failures == 1
    user = r.user(1)
    assert "3. detect_object(target=chair) [abandoned]" in user
    assert "failure 1 of 3" in user
    assert reasons(o)[1] == "failure"


def test_failure_budget(tmp_path, registry):
    r = Rig(tmp_path, registry, [plan(walk())] * 3, [exec_result("error")] * 3,
            loop={"max_failures": 3})
    o = r.run()
    assert o.outcome == "FAILURE_BUDGET_EXHAUSTED"
    assert len(r.planner.calls) == 3
    assert o.failures == 3


def test_checkpoints_never_count(tmp_path, registry):
    items = [plan(walk(0.2), walk(0.2), replan_after=1)] * 5 + [done()]
    r = Rig(tmp_path, registry, items, [exec_result()] * 5, loop={"max_failures": 1})
    o = r.run()
    assert o.outcome == "DONE"
    assert o.failures == 0


def test_call_budget(tmp_path, registry):
    r = Rig(tmp_path, registry, [plan(walk(0.2))] * 4, [exec_result()] * 4,
            loop={"max_llm_calls": 4})
    o = r.run()
    assert o.outcome == "CALL_BUDGET_EXHAUSTED"
    assert len(r.planner.calls) == 4 and o.llm_calls == 4


def test_schema_retry_success(tmp_path, registry):
    bad = {"status": "PLAN", "steps": []}
    r = Rig(tmp_path, registry, [bad, plan(walk()), done()], [exec_result()])
    o = r.run()
    assert o.outcome == "DONE"
    assert o.llm_calls == 3
    assert o.failures == 0
    retry = records(o, "llm_request")[1]
    assert (retry["return_reason"], retry["retry_of"]) == ("schema_retry", "initial")
    assert r.user(1).startswith(r.user(0) + "\n\n" + prompts.REJECTION_HEADER)
    assert len(records(o, "plan_invalid")) == 1


def test_schema_retry_failure(tmp_path, registry):
    bad = {"status": "PLAN", "steps": []}
    r = Rig(tmp_path, registry, [bad, bad])
    o = r.run()
    assert o.outcome == "LLM_INVALID"
    assert o.llm_calls == 2


def test_horizon_rejection(tmp_path, registry):
    too_long = {"status": "PLAN", "steps": [walk().model_dump()] * 3}
    r = Rig(tmp_path, registry, [too_long, plan(turn()), done()], [exec_result()],
            loop={"planning_horizon": 2})
    o = r.run()
    assert o.outcome == "DONE"
    hr = records(o, "horizon_rejection")
    assert len(hr) == 1 and hr[0]["steps_in_plan"] == 3 and hr[0]["horizon"] == 2
    assert [c["skill"] for c in r.executor.runs] == ["turn"]
    assert records(o, "task_end")[0]["horizon_rejections"] == 1


def test_bounds_rejection(tmp_path, registry):
    r = Rig(tmp_path, registry, [plan(walk(), turn(), walk(99)), done()])
    o = r.run()
    assert r.executor.runs == []
    assert o.failures == 1
    assert o.steps[0].outcome == "rejected" and o.steps[0].plan_step == 3
    user = r.user(1)
    for line in ("1. walk(direction=forward, distance_m=0.5) [abandoned]",
                 "2. turn(direction=left, angle_deg=90) [abandoned]",
                 "3. walk(direction=forward, distance_m=99) [abandoned]"):
        assert line in user
    assert "- rejected before running: walk(" in user


def test_motion_budget_rejection(tmp_path, registry):
    r = Rig(tmp_path, registry, [plan(walk(1.5), walk(1.0)), done()],
            motion_budget={"max_distance_m": 2})
    o = r.run()
    assert r.executor.runs == []
    rej = o.steps[0]
    assert (rej.outcome, rej.plan_step) == ("motion_budget_exceeded", 2)
    assert "travel" in rej.error_message and "0.5" in rej.error_message
    assert o.failures == 1


def test_budget_charged_on_error(tmp_path, registry):
    r = Rig(tmp_path, registry, [plan(walk(1.5)), done()], [exec_result("error")])
    o = r.run()
    assert "Travel: 1.5 of 10 m used" in r.user(1)
    assert records(o, "step_result")[0]["budget_used"]["distance_m"] == 1.5


def test_llm_unavailable(tmp_path, registry):
    r = Rig(tmp_path, registry, [LLMUnavailable("APIStatusError 500")])
    o = r.run()
    assert o.outcome == "LLM_ERROR"
    assert "APIStatusError 500" in o.message
    assert len(records(o, "llm_error")) == 1


def test_llm_interrupted_operator(tmp_path, registry):
    r = Rig(tmp_path, registry, [LLMInterrupted("operator")])
    o = r.run()
    assert o.outcome == "STOPPED"
    assert r.executor.stop_moves == ["operator"]
    assert len(records(o, "llm_interrupted")) == 1


def test_stop_during_step(tmp_path, registry):
    res = exec_result("interrupted", interrupt_cause="operator",
                      stop_move=stop_move_result("operator", posture="standing"))
    r = Rig(tmp_path, registry, [plan(walk(), turn())], [res])
    o = r.run()
    assert o.outcome == "STOPPED"
    assert o.failures == 0  # interrupted is not a failure
    assert len(r.planner.calls) == 1
    assert r.executor.stop_moves == []
    sm = records(o, "stop_move")
    assert len(sm) == 1 and sm[0]["ok"] is True and sm[0]["reason"] == "operator"


def test_stop_between_steps(tmp_path, registry):
    def step1(call):
        call["stop_event"].set()
        return exec_result()

    r = Rig(tmp_path, registry, [plan(walk(), turn())], [step1])
    o = r.run()
    assert o.outcome == "STOPPED"
    assert len(r.executor.runs) == 1
    assert r.executor.stop_moves == ["operator"]


def test_stop_during_llm_call_skips_retry(tmp_path, registry):
    planner_ref = {}

    def on_call(call_index):
        planner_ref["p"].calls[-1]["stop_event"].set()

    r = Rig(tmp_path, registry, [{"status": "PLAN", "steps": []}, done()], on_call=on_call)
    planner_ref["p"] = r.planner
    o = r.run()
    assert o.outcome == "STOPPED"
    assert len(r.planner.calls) == 1
    assert r.executor.stop_moves == ["operator"]


def test_request_stop_idle(tmp_path, registry):
    r = Rig(tmp_path, registry, [])
    assert r.d.request_stop("test") == "idle"


def test_time_limit_between_steps(tmp_path, registry):
    rig = {}

    def step1(call):
        rig["r"].clock.advance(rig["r"].cfg.loop.task_time_limit_s + 1)
        return exec_result()

    r = Rig(tmp_path, registry, [plan(walk(), turn())], [step1])
    rig["r"] = r
    o = r.run()
    assert o.outcome == "TIME_LIMIT_EXCEEDED"
    assert r.executor.stop_moves == ["task_time_limit"]
    assert len(r.executor.runs) == 1


def _blocking_step(entered: threading.Event, release: threading.Event):
    def step(call):
        entered.set()
        release.wait(WAIT_S)
        return exec_result()
    return step


def test_busy(tmp_path, registry):
    entered, release = threading.Event(), threading.Event()
    r = Rig(tmp_path, registry, [plan(walk()), done()], [_blocking_step(entered, release)])
    out = {}
    th = threading.Thread(target=lambda: out.setdefault("o", r.run()))
    th.start()
    assert entered.wait(WAIT_S)
    assert r.d.is_busy()
    with pytest.raises(BusyError):
        r.d.run_task("another", source="test")
    assert len(r.planner.calls) == 1
    release.set()
    th.join(WAIT_S)
    assert out["o"].outcome == "DONE"
    assert not r.d.is_busy()


def test_internal_error_then_next_task(tmp_path, registry):
    r = Rig(tmp_path, registry, [plan(walk()), done()], [RuntimeError("boom")])
    o = r.run()
    assert o.outcome == "INTERNAL_ERROR"
    assert "RuntimeError" in o.message and o.run_id in o.message
    assert r.executor.kills == ["shutdown"]
    assert r.executor.stop_moves == ["internal_error"]
    assert len(records(o, "exception")) == 1
    assert not r.d.is_busy()
    assert not r.d._stop_event.is_set()
    assert r.run("next").outcome == "DONE"


def test_internal_error_stop_move_and_kill_raise_task_end_still_written(tmp_path, registry):
    class RaisingExecutor(FakeExecutor):
        def kill_current(self, cause):
            super().kill_current(cause)
            raise OSError("kill failed")

        def stop_move(self, reason):
            self.stop_moves.append(reason)
            raise RuntimeError("stop_move exploded")

    ex = RaisingExecutor([RuntimeError("boom")])
    r = Rig(tmp_path, registry, [plan(walk()), done()], executor=ex)
    o = r.run()
    assert o.outcome == "INTERNAL_ERROR"
    assert o.stop_move_failed
    assert ex.stop_moves == ["internal_error"]
    (sm,) = records(o, "stop_move")
    assert sm["ok"] is False and "stop_move exploded" in sm["stderr_tail"]
    (end,) = records(o, "task_end")
    assert end["outcome"] == "INTERNAL_ERROR" and end["stop_move_failed"] is True
    index = r.cfg.log.dir / "index.jsonl"
    rows = [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["run_id"] == o.run_id and rows[-1]["outcome"] == "INTERNAL_ERROR"
    assert not r.d.is_busy()


def test_internal_error_stop_move_stderr_tail_is_cut(tmp_path, registry):
    class RaisingExecutor(FakeExecutor):
        def stop_move(self, reason):
            self.stop_moves.append(reason)
            raise RuntimeError("x" * (STDERR_TAIL_CHARS * 2))

    ex = RaisingExecutor([RuntimeError("boom")])
    r = Rig(tmp_path, registry, [plan(walk()), done()], executor=ex)
    o = r.run()
    assert o.outcome == "INTERNAL_ERROR"
    (sm,) = records(o, "stop_move")
    assert len(sm["stderr_tail"]) == STDERR_TAIL_CHARS
    assert sm["stderr_tail"].endswith("x")


def test_task_end_write_fails_index_row_still_written(tmp_path, registry, monkeypatch, capsys):
    real_write = RunLog.write

    def write(self, type_, **fields):
        if type_ == "task_end":
            raise OSError("disk full")
        return real_write(self, type_, **fields)

    monkeypatch.setattr(RunLog, "write", write)
    r = Rig(tmp_path, registry, [done()])
    o = r.run()
    assert o.outcome == "DONE"
    assert records(o, "task_end") == []
    index = r.cfg.log.dir / "index.jsonl"
    rows = [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["run_id"] == o.run_id and rows[0]["outcome"] == "DONE"
    err = capsys.readouterr().err
    assert "task_end" in err and "OSError: disk full" in err
    assert not r.d.is_busy()


def test_previous_task(tmp_path, registry):
    r = Rig(tmp_path, registry, [done("first done"), done("second done")])
    r.run("first task")
    r.run("second task")
    assert "Task: first task\nOutcome: DONE\nMessage: first done" in r.user(1)
    assert r.d.previous.task == "second task"


def test_posture(tmp_path, registry):
    sit = PlanStep(skill="sit", params={})
    timeout = exec_result("timeout", stop_move=stop_move_result("step_timeout"))
    r = Rig(tmp_path, registry, [plan(sit), done(), plan(walk()), done()],
            [exec_result(skill="sit", posture="sitting"), timeout])
    r.run("sit down")
    assert "Posture: sitting" in r.user(1)
    r.run("walk")
    assert "Posture: sitting" in r.user(2)
    assert "Posture: unknown" in r.user(3)
    assert r.d.posture == "unknown"


def test_stop_move_failure_warns(tmp_path, registry):
    def step1(call):
        call["stop_event"].set()
        return exec_result()

    r = Rig(tmp_path, registry, [plan(walk(), turn())],
            executor=FakeExecutor([step1], stop_move_ok=False))
    o = r.run()
    assert o.outcome == "STOPPED"
    assert o.stop_move_failed is True
    assert o.message.endswith(prompts.STOP_MOVE_WARNING)


def test_fault_lookup(tmp_path, registry):
    r = Rig(tmp_path, registry, [plan(walk(), turn(), detect()), done()], [exec_result()] * 3,
            stub={"faults": [{"step": 2, "kind": "error"}]})
    r.run()
    assert [c["fault"] for c in r.executor.runs] == [None, "error", None]


def test_shutdown_while_step_blocks(tmp_path, registry):
    entered, release = threading.Event(), threading.Event()

    def on_kill(cause):
        if cause == "shutdown":
            release.set()

    executor = FakeExecutor([_blocking_step(entered, release)], on_kill=on_kill)
    r = Rig(tmp_path, registry, [plan(walk())], executor=executor)
    out = {}
    th = threading.Thread(target=lambda: out.setdefault("o", r.run()))
    th.start()
    assert entered.wait(WAIT_S)
    r.d.shutdown(0.2)
    assert executor.kills == ["operator", "shutdown"]
    assert "shutdown" in executor.stop_moves
    th.join(WAIT_S)
    assert out["o"].outcome == "STOPPED"
    assert records(out["o"], "stop_requested")[0]["source"] == "shutdown"


# --- branches not reached by the tests above --------------------------------------------


def test_invalid_reply_at_call_budget_skips_retry(tmp_path, registry):
    r = Rig(tmp_path, registry, [{"status": "PLAN", "steps": []}], loop={"max_llm_calls": 1})
    o = r.run()
    assert o.outcome == "CALL_BUDGET_EXHAUSTED"
    assert len(r.planner.calls) == 1


def test_precheck_rejection_reaches_failure_budget(tmp_path, registry):
    r = Rig(tmp_path, registry, [plan(walk(99))], loop={"max_failures": 1})
    o = r.run()
    assert o.outcome == "FAILURE_BUDGET_EXHAUSTED"
    assert r.executor.runs == []


def test_stop_during_schema_retry_call(tmp_path, registry):
    planner_ref = {}

    def on_call(call_index):
        if call_index == 2:
            planner_ref["p"].calls[-1]["stop_event"].set()

    r = Rig(tmp_path, registry, [{"status": "PLAN", "steps": []}, plan(walk())],
            on_call=on_call)
    planner_ref["p"] = r.planner
    o = r.run()
    assert o.outcome == "STOPPED"
    assert len(r.planner.calls) == 2
    assert r.executor.runs == []


def test_step_stop_move_failure_warns(tmp_path, registry):
    timeout = exec_result("timeout", stop_move=stop_move_result("step_timeout", ok=False))
    r = Rig(tmp_path, registry, [plan(walk()), done()], [timeout])
    o = r.run()
    assert o.outcome == "DONE"
    assert o.stop_move_failed is True
    assert o.message.endswith(prompts.STOP_MOVE_WARNING)


def test_dispatcher_stop_move_updates_posture(tmp_path, registry):
    def step1(call):
        call["stop_event"].set()
        return exec_result()

    r = Rig(tmp_path, registry, [plan(walk(), turn())],
            executor=FakeExecutor([step1], stop_move_posture="sitting"))
    o = r.run()
    assert o.outcome == "STOPPED"
    assert r.d.posture == o.final_posture == "sitting"


def test_llm_interrupted_time_limit(tmp_path, registry):
    r = Rig(tmp_path, registry, [LLMInterrupted("task_time_limit")])
    o = r.run()
    assert o.outcome == "TIME_LIMIT_EXCEEDED"
    assert r.executor.stop_moves == ["task_time_limit"]


def test_shutdown_idle_is_noop(tmp_path, registry):
    r = Rig(tmp_path, registry, [])
    r.d.shutdown(1.0)
    assert (r.executor.kills, r.executor.stop_moves) == ([], [])


def test_shutdown_task_ends_within_wait(tmp_path, registry):
    """The operator kill ends the step in time: no shutdown kill and no extra StopMove."""
    entered, release = threading.Event(), threading.Event()
    executor = FakeExecutor([_blocking_step(entered, release)], on_kill=lambda c: release.set())
    r = Rig(tmp_path, registry, [plan(walk())], executor=executor)
    out = {}
    th = threading.Thread(target=lambda: out.setdefault("o", r.run()))
    th.start()
    assert entered.wait(WAIT_S)
    r.d.shutdown(WAIT_S)
    th.join(WAIT_S)
    assert out["o"].outcome == "STOPPED"
    assert executor.kills == ["operator"]
    assert executor.stop_moves == ["operator"]
