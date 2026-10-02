"""Dispatcher loop: plan -> validate -> execute -> feed back (dispatcher/docs/loop-and-context.md)."""

from __future__ import annotations

import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from datetime import datetime
from typing import Any, Literal

from . import prompts
from .bounds import cut_message, precheck
from .budget import MotionBudget
from .config import Config
from .context import ContextInput, build_user_message, schema_retry_message
from .executor import STDERR_TAIL_CHARS, Executor
from .llm import LLMResult, PlannerClient
from .models import (
    FAILURE_OUTCOMES,
    BusyError,
    LLMInterrupted,
    LLMUnavailable,
    PlanStep,
    StepResult,
    StopMoveResult,
    TaskOutcome,
    TaskOutcomeCode,
    TaskSummary,
)
from .registry import Registry
from .runlog import NullLog, RunLog, RunLogFactory

__all__ = ["Dispatcher"]

KILLED_OUTCOMES = frozenset({"timeout", "interrupted"})
STOP_CAUSES = frozenset({"operator", "shutdown"})  # interrupted -> STOPPED; else TIME_LIMIT
EXCEPTION_WHERE = "run_task"

Phase = Literal["idle", "llm_call", "step", "between", "ending"]


def _numeric_usage(usage: dict) -> dict[str, float]:
    return {k: v for k, v in usage.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}


# --- per-task state (dispatcher/docs/loop-and-context.md) -----------------------------------------------------------


@dataclass
class _Task:
    run_id: str
    task: str
    source: str
    sender_id: str | None
    t_start: float
    ts_start: str
    deadline: float
    budget: MotionBudget
    log: RunLog = field(default_factory=NullLog)
    steps: list[StepResult] = field(default_factory=list)
    dispatched_count: int = 0
    failures: int = 0
    llm_calls: int = 0
    rejections: int = 0
    horizon_rejections: int = 0
    remaining: list[tuple[int, PlanStep]] = field(default_factory=list)
    remaining_tag: Literal["pending", "abandoned"] | None = None
    return_reason: str = "initial"
    notice_args: dict = field(default_factory=dict)
    last_failure: StepResult | None = None
    stop_move_failed: bool = False
    usage_totals: dict[str, float] = field(default_factory=dict)


class Dispatcher:
    def __init__(
        self,
        cfg: Config,
        registry: Registry,
        planner: PlannerClient,
        executor: Executor,
        runlog_factory: RunLogFactory,
        *,
        clock: Callable[[], float] = time.monotonic,
        initial_posture: str = "unknown",
    ):
        self.cfg = cfg
        self.registry = registry
        self.planner = planner
        self.executor = executor
        self.runlog_factory = runlog_factory
        self.clock = clock
        self.posture = initial_posture
        self.previous: TaskSummary | None = None

        self._task_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._phase: Phase = "idle"
        self._stop_event = threading.Event()
        self._log: RunLog | None = None

        # the prompt surface is the one the run log records (dispatcher/docs/run-log.md)
        session = runlog_factory.session
        self._system = prompts.system_blocks(cfg.loop.planning_horizon, session.catalog_text)
        self._tool_schema = session.tool_schema
        self.registry_hash = session.registry_hash

    # --- public interface (dispatcher/docs/loop-and-context.md) ------------------------------------------------------

    def is_busy(self) -> bool:
        return self._task_lock.locked()

    def request_stop(self, source: str) -> Literal["stopping", "idle"]:
        with self._state_lock:
            if self._phase in ("idle", "ending"):
                return "idle"
            self._stop_event.set()
            if self._log is not None:
                self._log.stop_requested(source, self._phase)
        self.executor.kill_current("operator")
        return "stopping"

    def shutdown(self, wait_s: float) -> None:
        if not self.is_busy():
            return
        self.request_stop("shutdown")
        acquired = self._task_lock.acquire(timeout=wait_s) if wait_s > 0 else self._task_lock.acquire(blocking=False)
        if acquired:
            self._task_lock.release()
            return
        self.executor.kill_current("shutdown")
        smr = self.executor.stop_move("shutdown")
        with self._state_lock:
            if self._log is not None:
                self._log.stop_move(smr)

    def run_task(self, task: str, *, source: str, sender_id: str | None = None) -> TaskOutcome:
        if not task.strip():
            raise ValueError("empty task")
        if not self._task_lock.acquire(blocking=False):
            raise BusyError("a task is running")
        try:
            t = self._new_task(task, source, sender_id)
            try:
                self._open_log(t)
                return self._loop(t)
            except Exception as e:  # noqa: BLE001 - every unhandled error ends the task (dispatcher/docs/loop-and-context.md)
                return self._internal_error(t, e)
        finally:
            with self._state_lock:
                self._phase = "idle"
                self._stop_event.clear()
                log, self._log = self._log, None
            if log is not None:
                log.close()
            self._task_lock.release()

    # --- task setup -------------------------------------------------------------------------

    def _new_task(self, task: str, source: str, sender_id: str | None) -> _Task:
        now = self.clock()
        mb = self.cfg.motion_budget
        return _Task(
            run_id=uuid.uuid4().hex,
            task=task,
            source=source,
            sender_id=sender_id,
            t_start=now,
            ts_start=datetime.now().astimezone().isoformat(),
            deadline=now + self.cfg.loop.task_time_limit_s,
            budget=MotionBudget(mb.max_distance_m, mb.max_rotation_deg),
        )

    def _open_log(self, t: _Task) -> None:
        log = self.runlog_factory.open(t.run_id, t.t_start, clock=self.clock)
        with self._state_lock:
            t.log = log
            self._log = log
            self._phase = "between"
        log.task_start(t.task, t.source, t.sender_id, self.previous, self.posture)

    # --- helpers ------------------------------------------------------------------------------

    def _set_phase(self, phase: Phase) -> None:
        with self._state_lock:
            self._phase = phase

    def _remaining_s(self, t: _Task) -> float:
        return t.deadline - self.clock()

    def _record(self, t: _Task, sr: StepResult, stop: StopMoveResult | None = None) -> None:
        """Append a step, update posture and the StopMove flag, log it (and the StopMove of a killed step)."""
        t.steps.append(sr)
        if sr.index is not None:
            self._update_posture_from_step(sr, stop)
        if stop is not None and not stop.ok:
            t.stop_move_failed = True
        if sr.outcome in FAILURE_OUTCOMES:
            t.failures += 1
            t.last_failure = sr
        if sr.index is None:
            t.rejections += 1
        t.log.step_result(sr, budget=t.budget, failures=t.failures, posture=self.posture)
        if stop is not None:
            t.log.stop_move(stop)

    def _update_posture_from_step(self, sr: StepResult, stop: StopMoveResult | None) -> None:
        """Last known posture from the step's state_after (dispatcher/docs/loop-and-context.md)."""
        if sr.response is not None and sr.response.state_after is not None:
            self.posture = sr.response.state_after.posture
        elif stop is not None and stop.response is not None and stop.response.state_after is not None:
            self.posture = stop.response.state_after.posture
        elif sr.outcome in KILLED_OUTCOMES:
            self.posture = "unknown"

    def _context(self, t: _Task) -> str:
        loop = self.cfg.loop
        # ContextInput fields that _Task holds under the same name
        shared = {f.name: getattr(t, f.name) for f in fields(ContextInput) if hasattr(t, f.name)}
        return build_user_message(
            ContextInput(
                **shared,
                posture=self.posture,
                max_failures=loop.max_failures,
                max_llm_calls=loop.max_llm_calls,
                history_k=loop.context_history_k,
                previous=self.previous,
            ),
            self.registry,
        )

    # --- LLM call ------------------------------------------------------------------------------

    def _call(self, t: _Task, user: str, return_reason: str, retry_of: str | None = None) -> LLMResult | TaskOutcome:
        """One planner call. Returns the result, or the task outcome if the call ended it
        (interrupted, unavailable, or a stop/deadline that arrived during the call)."""
        call_index = t.llm_calls + 1
        self._set_phase("llm_call")
        t.log.llm_request(call_index, return_reason, retry_of, user)
        try:
            res = self.planner.plan(
                system=list(self._system),
                user=user,
                tool_schema=self._tool_schema,
                call_index=call_index,
                remaining_s=lambda: self._remaining_s(t),
                stop_event=self._stop_event,
                on_infra_retry=t.log.llm_retry,
            )
        except LLMInterrupted as e:
            self._set_phase("between")
            t.log.llm_interrupted(call_index, e.cause)
            return self._end(t, "STOPPED" if e.cause == "operator" else "TIME_LIMIT_EXCEEDED", stop_move=True)
        except LLMUnavailable as e:
            self._set_phase("between")
            t.log.llm_error(call_index, e.detail)
            return self._end(t, "LLM_ERROR", detail=e.detail)
        except BaseException:
            self._set_phase("between")
            raise
        self._set_phase("between")

        t.llm_calls += 1
        for k, v in _numeric_usage(res.usage).items():
            t.usage_totals[k] = t.usage_totals.get(k, 0) + v
        t.log.llm_response(call_index, res)
        if (o := self._check_interrupts(t)) is not None:
            return o
        return res

    def _log_invalid(self, t: _Task, res: LLMResult) -> None:
        if res.rejection_kind == "horizon":
            t.horizon_rejections += 1
        t.log.plan_invalid(t.llm_calls, res, self.cfg.loop.planning_horizon)

    # --- interrupts and end ----------------------------------------------------------------------

    def _check_interrupts(self, t: _Task) -> TaskOutcome | None:
        if self._stop_event.is_set():
            return self._end(t, "STOPPED", stop_move=True)
        if self._remaining_s(t) <= 0:
            return self._end(t, "TIME_LIMIT_EXCEEDED", stop_move=True)
        return None

    def _operator_args(self, t: _Task, outcome: TaskOutcomeCode, message: str | None, extra: dict) -> dict:
        loop = self.cfg.loop
        if outcome in ("DONE", "ABORTED"):
            return {"message": message or ""}
        if outcome == "TIME_LIMIT_EXCEEDED":
            return {"limit": loop.task_time_limit_s}
        if outcome == "FAILURE_BUDGET_EXHAUSTED":
            lf = t.last_failure
            return {
                "n": t.failures,
                "skill": lf.skill if lf else "",
                "error_message": (lf.error_message or lf.outcome) if lf else "",
            }
        if outcome == "CALL_BUDGET_EXHAUSTED":
            return {"n": loop.max_llm_calls}
        if outcome == "LLM_ERROR":
            return {"detail": extra.get("detail", "")}
        if outcome == "INTERNAL_ERROR":
            return {"exception_type": extra.get("exception_type", "Exception"), "run_id": t.run_id}
        return {}

    def _apply_stop_move(self, t: _Task, smr: StopMoveResult) -> None:
        t.log.stop_move(smr)
        if smr.response is not None and smr.response.state_after is not None:
            self.posture = smr.response.state_after.posture
        if not smr.ok:
            t.stop_move_failed = True

    def _end(
        self,
        t: _Task,
        outcome: TaskOutcomeCode,
        *,
        message: str | None = None,
        stop_move: bool = False,
        stop_move_result: StopMoveResult | None = None,
        **extra: Any,
    ) -> TaskOutcome:
        self._set_phase("ending")
        if stop_move:
            reason = "operator" if outcome == "STOPPED" else "task_time_limit"
            self._apply_stop_move(t, self.executor.stop_move(reason))
        if stop_move_result is not None:
            self._apply_stop_move(t, stop_move_result)

        text = prompts.operator_message(
            outcome, stop_move_failed=t.stop_move_failed, **self._operator_args(t, outcome, message, extra)
        )
        duration_ms = (self.clock() - t.t_start) * 1000.0
        dispatched = [s for s in t.steps if s.index is not None]
        result = TaskOutcome(
            run_id=t.run_id,
            task=t.task,
            outcome=outcome,
            message=text,
            steps=list(t.steps),
            llm_calls=t.llm_calls,
            failures=t.failures,
            stop_move_failed=t.stop_move_failed,
            duration_ms=duration_ms,
            final_posture=self.posture,
            log_path=str(t.log.path),
        )
        try:
            t.log.task_end(
                result,
                rejections=t.rejections,
                horizon_rejections=t.horizon_rejections,
                usage_totals=t.usage_totals,
                budget=t.budget,
            )
        except Exception as e:  # noqa: BLE001 - the index row must still be written (dispatcher/docs/loop-and-context.md)
            print(
                f"go2-dispatcher: failed to write task_end for run {t.run_id}: {type(e).__name__}: {e}", file=sys.stderr
            )
        finally:
            if not isinstance(t.log, NullLog):
                self.runlog_factory.append_index(
                    t.log, result, ts_start=t.ts_start, source=t.source, usage_totals=t.usage_totals
                )
        self.previous = TaskSummary(
            task=t.task, outcome=outcome, message=text, last_step=dispatched[-1] if dispatched else None
        )
        return result

    def _internal_error(self, t: _Task, e: Exception) -> TaskOutcome:
        """Nothing may escape before task_end and the index row are written (dispatcher/docs/loop-and-context.md)."""
        smr: StopMoveResult | None = None
        try:
            try:
                t.log.exception(EXCEPTION_WHERE, e)
            except Exception:  # noqa: BLE001
                pass
            try:
                self.executor.kill_current("shutdown")
            except Exception:  # noqa: BLE001
                pass
            t0 = self.clock()
            try:
                smr = self.executor.stop_move("internal_error")
            except Exception as se:  # noqa: BLE001 - Executor.stop_move never raises; fakes might
                smr = StopMoveResult(
                    ok=False,
                    reason="internal_error",
                    duration_ms=(self.clock() - t0) * 1000.0,
                    stderr_tail=f"{type(se).__name__}: {se}"[-STDERR_TAIL_CHARS:],
                )
        finally:
            # also runs if a BaseException (e.g. KeyboardInterrupt) arrives above; it then propagates
            outcome = self._end(t, "INTERNAL_ERROR", stop_move_result=smr, exception_type=type(e).__name__)
        return outcome

    # --- the loop (dispatcher/docs/loop-and-context.md) --------------------------------------------------------------

    def _loop(self, t: _Task) -> TaskOutcome:
        loop = self.cfg.loop
        while True:
            if (o := self._check_interrupts(t)) is not None:
                return o
            if t.llm_calls >= loop.max_llm_calls:
                return self._end(t, "CALL_BUDGET_EXHAUSTED")

            user = self._context(t)
            res = self._call(t, user, t.return_reason)
            if isinstance(res, TaskOutcome):
                return res

            if res.errors:
                self._log_invalid(t, res)
                if t.llm_calls >= loop.max_llm_calls:
                    return self._end(t, "CALL_BUDGET_EXHAUSTED")
                res = self._call(t, schema_retry_message(user, res.errors), "schema_retry", retry_of=t.return_reason)
                if isinstance(res, TaskOutcome):
                    return res
                if res.errors:
                    self._log_invalid(t, res)
                    return self._end(t, "LLM_INVALID")

            plan = res.plan
            stop_at = plan.replan_after or len(plan.steps)
            t.log.plan(t.llm_calls, plan, stop_at)
            if plan.status == "DONE":
                return self._end(t, "DONE", message=plan.message)
            if plan.status == "ABORT":
                return self._end(t, "ABORTED", message=plan.message)

            pre = precheck(plan, stop_at, self.registry, t.budget, call_index=t.llm_calls)
            if pre.rejection is not None:
                rej = pre.rejection
                self._record(t, rej)
                t.remaining = list(enumerate(plan.steps, start=1))
                t.remaining_tag = "abandoned"
                t.notice_args = {"n": rej.plan_step, "skill": rej.skill, "outcome": rej.outcome, "f": t.failures}
                if t.failures >= loop.max_failures:
                    return self._end(t, "FAILURE_BUDGET_EXHAUSTED")
                t.return_reason = "failure"
                continue

            failed = False
            for i, step in enumerate(plan.steps[:stop_at], start=1):
                if (o := self._check_interrupts(t)) is not None:
                    return o
                o = self._run_step(t, plan.steps, i, step, pre.filled[i - 1])
                if isinstance(o, TaskOutcome):
                    return o
                if o:
                    failed = True
                    break

            if failed:
                if t.failures >= loop.max_failures:
                    return self._end(t, "FAILURE_BUDGET_EXHAUSTED")
                t.return_reason = "failure"
            elif stop_at < len(plan.steps):
                t.remaining = [(j, s) for j, s in enumerate(plan.steps, start=1) if j > stop_at]
                t.remaining_tag = "pending"
                t.notice_args = {"n": stop_at}
                t.return_reason = "checkpoint"
            else:
                t.remaining = []
                t.remaining_tag = None
                t.notice_args = {}
                t.return_reason = "plan_complete"

    def _run_step(self, t: _Task, steps: list[PlanStep], i: int, step: PlanStep, params: dict) -> TaskOutcome | bool:
        """Dispatch plan step ``i``. Returns the task outcome if the task ended, else
        whether the step failed."""
        desc = self.registry.get(step.skill)
        cost = desc.policy.motion_cost(params)
        timeout = desc.policy.timeout_s(params)
        t.dispatched_count += 1
        fault = None
        if self.cfg.robot.backend == "stub":
            fault = next((f.kind for f in self.cfg.stub.faults if f.step == t.dispatched_count), None)
        t.budget.charge(cost)

        self._set_phase("step")
        t.log.step_start(
            index=t.dispatched_count,
            call_index=t.llm_calls,
            plan_step=i,
            skill=step.skill,
            params=params,
            timeout_s=timeout,
            cost=cost,
            fault=fault,
        )
        try:
            ex = self.executor.run(
                desc,
                params,
                fault=fault,
                timeout_s=timeout,
                remaining_task_s=self._remaining_s(t),
                stop_event=self._stop_event,
            )
        finally:
            self._set_phase("between")

        exec_fields = {k: v for k, v in ex if k not in ("interrupt_cause", "stop_move")}  # the rest maps 1:1
        exec_fields["error_message"] = cut_message(ex.error_message) if ex.error_message else None
        sr = StepResult(
            **exec_fields,
            index=t.dispatched_count,
            call_index=t.llm_calls,
            plan_step=i,
            skill=step.skill,
            params=params,
            timeout_s=timeout,
            motion_cost=cost,
            fault=fault,
        )
        self._record(t, sr, ex.stop_move)

        if sr.outcome == "interrupted":
            # StopMove was already sent by the executor
            return self._end(t, "STOPPED" if ex.interrupt_cause in STOP_CAUSES else "TIME_LIMIT_EXCEEDED")
        if sr.outcome in FAILURE_OUTCOMES:
            t.remaining = [(j, s) for j, s in enumerate(steps, start=1) if j > i]
            t.remaining_tag = "abandoned"
            t.notice_args = {"n": i, "skill": sr.skill, "outcome": sr.outcome, "f": t.failures}
            return True
        return False
