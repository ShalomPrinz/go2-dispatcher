"""Dispatcher loop: plan -> validate -> execute -> feed back (dispatcher/docs/loop-and-context.md)."""

from __future__ import annotations

import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from . import prompts
from .bounds import cut_message, precheck
from .budget import MotionBudget
from .config import Config
from .context import ContextInput, Feedback, RequestReason, ReturnReason, build_user_message, schema_retry_message
from .executor import STDERR_TAIL_CHARS, SkillExecutor
from .llm import LLMResult, PlanCheck, PlannerClient, check_reply
from .models import (
    FAILURE_OUTCOMES,
    KILLED_OUTCOMES,
    BusyError,
    LLMInterrupted,
    LLMUnavailable,
    Plan,
    Posture,
    StepDispatch,
    StepRef,
    StepResult,
    StopCause,
    StopMoveResult,
    TaskOutcome,
    TaskOutcomeCode,
    TaskSummary,
)
from .registry import Registry
from .runlog import NullLog, RunLog, RunLogFactory, SessionInfo, TaskLog

__all__ = ["Dispatcher"]

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
    log: TaskLog = field(default_factory=NullLog)
    steps: list[StepResult] = field(default_factory=list)
    dispatched_count: int = 0
    failures: int = 0
    llm_calls: int = 0
    rejections: int = 0
    horizon_rejections: int = 0
    feedback: Feedback = field(default_factory=Feedback)
    last_failure: StepResult | None = None
    stop_move_failed: bool = False
    usage_totals: dict[str, float] = field(default_factory=dict)


class Dispatcher:
    def __init__(
        self,
        cfg: Config,
        registry: Registry,
        planner: PlannerClient,
        executor: SkillExecutor,
        *,
        clock: Callable[[], float] = time.monotonic,
        initial_posture: Posture = "unknown",
    ):
        self.cfg = cfg
        self.registry = registry
        self.planner = planner
        self.executor = executor
        # the log copies the surface the planner sends (dispatcher/docs/run-log.md)
        self.runlog_factory = RunLogFactory(cfg.log.dir, uuid.uuid4().hex, SessionInfo.collect(cfg, planner.surface))
        self.registry_hash = planner.surface.registry_hash
        self.clock = clock
        self.posture: Posture = initial_posture
        self.previous: TaskSummary | None = None

        self._task_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._phase: Phase = "idle"
        self._stop_event = threading.Event()
        self._log: RunLog | None = None

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
        self.executor.kill_current(StopCause.OPERATOR)
        return "stopping"

    def shutdown(self, wait_s: float) -> None:
        if not self.is_busy():
            return
        self.request_stop("shutdown")
        acquired = self._task_lock.acquire(timeout=wait_s) if wait_s > 0 else self._task_lock.acquire(blocking=False)
        if acquired:
            self._task_lock.release()
            return
        self.executor.kill_current(StopCause.SHUTDOWN)
        smr = self.executor.stop_move(StopCause.SHUTDOWN)
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
        self._apply_state(t, sr, stop)
        if sr.outcome in FAILURE_OUTCOMES:
            t.failures += 1
            t.last_failure = sr
        if sr.dispatch is None:
            t.rejections += 1
        t.log.step_result(sr, budget=t.budget, failures=t.failures, posture=self.posture)
        if stop is not None:
            t.log.stop_move(stop)

    def _apply_state(self, t: _Task, sr: StepResult | None, stop: StopMoveResult | None) -> None:
        """Update the last known posture and the StopMove flag (dispatcher/docs/loop-and-context.md)."""
        for resp in (sr.response if sr else None, stop.response if stop else None):
            if resp is not None and resp.state_after is not None:
                self.posture = resp.state_after.posture  # the step's state_after wins over StopMove's
                break
        else:
            if sr is not None and sr.outcome in KILLED_OUTCOMES:
                self.posture = "unknown"
        if stop is not None and not stop.ok:
            t.stop_move_failed = True

    def _context(self, t: _Task) -> str:
        loop = self.cfg.loop
        return build_user_message(
            ContextInput(
                task=t.task,
                posture=self.posture,
                budget=t.budget,
                failures=t.failures,
                max_failures=loop.max_failures,
                llm_calls=t.llm_calls,
                max_llm_calls=loop.max_llm_calls,
                history_k=loop.context_history_k,
                steps=t.steps,
                feedback=t.feedback,
                previous=self.previous,
            ),
            self.registry,
        )

    # --- LLM call ------------------------------------------------------------------------------

    def _call(
        self, t: _Task, user: str, return_reason: RequestReason, retry_of: ReturnReason | None = None
    ) -> LLMResult | TaskOutcome:
        """One planner call. Returns the result, or the task outcome if the call ended it
        (interrupted, unavailable, or a stop/deadline that arrived during the call)."""
        call_index = t.llm_calls + 1
        self._set_phase("llm_call")
        t.log.llm_request(call_index, return_reason, retry_of, user)
        try:
            res = self.planner.plan(
                user=user,
                call_index=call_index,
                remaining_s=lambda: self._remaining_s(t),
                stop_event=self._stop_event,
                on_infra_retry=t.log.llm_retry,
            )
        except LLMInterrupted as e:
            self._set_phase("between")
            t.log.llm_interrupted(call_index, e.cause)
            return self._stop(t, e.cause)
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

    def _log_invalid(self, t: _Task, res: LLMResult, check: PlanCheck) -> None:
        if check.rejection_kind == "horizon":
            t.horizon_rejections += 1
        t.log.plan_invalid(t.llm_calls, res.tool_input, check, self.cfg.loop.planning_horizon)

    # --- interrupts and end ----------------------------------------------------------------------

    def _check_interrupts(self, t: _Task) -> TaskOutcome | None:
        if self._stop_event.is_set():
            return self._stop(t, StopCause.OPERATOR)
        if self._remaining_s(t) <= 0:
            return self._stop(t, StopCause.TASK_TIME_LIMIT)
        return None

    def _stop(self, t: _Task, cause: StopCause) -> TaskOutcome:
        """End the task for an interrupt seen by the loop and send StopMove."""
        self._set_phase("ending")
        return self._end(t, cause.task_outcome, stop_move=self.executor.stop_move(cause))

    def _end(
        self,
        t: _Task,
        outcome: TaskOutcomeCode,
        *,
        message: str | None = None,
        detail: str = "",
        exception_type: str = "Exception",
        stop_move: StopMoveResult | None = None,
    ) -> TaskOutcome:
        self._set_phase("ending")
        if stop_move is not None:
            t.log.stop_move(stop_move)
            self._apply_state(t, None, stop_move)

        loop = self.cfg.loop
        facts = prompts.OutcomeFacts(
            run_id=t.run_id,
            time_limit_s=loop.task_time_limit_s,
            max_llm_calls=loop.max_llm_calls,
            failures=t.failures,
            last_failure=t.last_failure,
            message=message,
            detail=detail,
            exception_type=exception_type,
        )
        text = prompts.operator_message(outcome, facts, stop_move_failed=t.stop_move_failed)
        duration_ms = (self.clock() - t.t_start) * 1000.0
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
            log_path=str(t.log.path) if t.log.path is not None else "",
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
            if t.log.path is not None:  # the index row points at the task's log file
                self.runlog_factory.append_index(
                    t.log.path, result, ts_start=t.ts_start, source=t.source, usage_totals=t.usage_totals
                )
        dispatched = result.dispatched_steps
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
                self.executor.kill_current(StopCause.SHUTDOWN)
            except Exception:  # noqa: BLE001
                pass
            t0 = self.clock()
            try:
                smr = self.executor.stop_move(StopCause.INTERNAL_ERROR)
            except Exception as se:  # noqa: BLE001 - Executor.stop_move never raises; fakes might
                smr = StopMoveResult(
                    ok=False,
                    reason=StopCause.INTERNAL_ERROR,
                    duration_ms=(self.clock() - t0) * 1000.0,
                    stderr_tail=f"{type(se).__name__}: {se}"[-STDERR_TAIL_CHARS:],
                )
        finally:
            # also runs if a BaseException (e.g. KeyboardInterrupt) arrives above; it then propagates
            outcome = self._end(t, "INTERNAL_ERROR", stop_move=smr, exception_type=type(e).__name__)
        return outcome

    # --- the loop (dispatcher/docs/loop-and-context.md) --------------------------------------------------------------

    def _loop(self, t: _Task) -> TaskOutcome:
        while True:
            if (o := self._check_interrupts(t)) is not None:
                return o
            plan = self._get_valid_plan(t)
            if isinstance(plan, TaskOutcome):
                return plan
            feedback = self._execute_plan(t, plan)
            if isinstance(feedback, TaskOutcome):
                return feedback
            if feedback.return_reason == "failure" and t.failures >= self.cfg.loop.max_failures:
                return self._end(t, "FAILURE_BUDGET_EXHAUSTED")
            t.feedback = feedback

    def _get_valid_plan(self, t: _Task) -> Plan | TaskOutcome:
        """The request, then at most one schema retry (dispatcher/docs/llm.md). Returns a valid plan, or the
        task outcome if the task ended."""
        user = self._context(t)
        request = user
        return_reason: RequestReason = t.feedback.return_reason
        retry_of: ReturnReason | None = None
        while True:
            if t.llm_calls >= self.cfg.loop.max_llm_calls:
                return self._end(t, "CALL_BUDGET_EXHAUSTED")
            res = self._call(t, request, return_reason, retry_of)
            if isinstance(res, TaskOutcome):
                return res
            check = check_reply(res, self.cfg.loop.planning_horizon)
            if check.plan is not None:
                return check.plan
            self._log_invalid(t, res, check)
            if retry_of is not None:
                return self._end(t, "LLM_INVALID")
            request, return_reason, retry_of = (
                schema_retry_message(user, check.errors),
                "schema_retry",
                t.feedback.return_reason,
            )

    def _execute_plan(self, t: _Task, plan: Plan) -> Feedback | TaskOutcome:
        """Run a valid plan up to ``stop_at``. Returns the feedback for the next call, or the task outcome if
        the task ended."""
        stop_at = plan.replan_after or len(plan.steps)
        t.log.plan(t.llm_calls, plan, stop_at)
        if plan.status == "DONE":
            return self._end(t, "DONE", message=plan.message)
        if plan.status == "ABORT":
            return self._end(t, "ABORTED", message=plan.message)

        pre = precheck(plan, stop_at, self.registry, t.budget, call_index=t.llm_calls)
        if pre.rejection is not None:
            self._record(t, pre.rejection)
            return Feedback.failure(plan.steps, pre.rejection, t.failures, ran=0)

        for i, step in enumerate(plan.steps[:stop_at], start=1):
            if (o := self._check_interrupts(t)) is not None:
                return o
            ref = StepRef(call_index=t.llm_calls, plan_step=i, skill=step.skill, params=pre.filled[i - 1])
            sr = self._run_step(t, ref)
            if isinstance(sr, TaskOutcome):
                return sr
            if sr.outcome in FAILURE_OUTCOMES:
                return Feedback.failure(plan.steps, sr, t.failures, ran=i)
        if stop_at < len(plan.steps):
            return Feedback.checkpoint(plan.steps, stop_at)
        return Feedback.plan_complete()

    def _run_step(self, t: _Task, ref: StepRef) -> StepResult | TaskOutcome:
        """Dispatch the step ``ref`` names. Returns its recorded result, or the task outcome if it was interrupted."""
        desc = self.registry[ref.skill]  # precheck passed, so the skill exists
        params = ref.params
        t.dispatched_count += 1
        dispatch = StepDispatch(
            index=t.dispatched_count,
            timeout_s=desc.policy.timeout_s(params),
            motion_cost=desc.policy.motion_cost(params),
            fault=self.cfg.stub.fault_at(t.dispatched_count),
        )
        t.budget.charge(dispatch.motion_cost)

        self._set_phase("step")
        t.log.step_start(ref, dispatch)
        try:
            ex = self.executor.run(
                desc,
                params,
                fault=dispatch.fault,
                timeout_s=dispatch.timeout_s,
                remaining_task_s=self._remaining_s(t),
                stop_event=self._stop_event,
            )
        finally:
            self._set_phase("between")

        exec_fields = {k: v for k, v in ex if k not in ("interrupt_cause", "stop_move")}  # the rest maps 1:1
        exec_fields["error_message"] = cut_message(ex.error_message) if ex.error_message else None
        sr = StepResult(ref=ref, dispatch=dispatch, **exec_fields)
        self._record(t, sr, ex.stop_move)

        if sr.outcome == "interrupted":
            assert ex.interrupt_cause is not None  # set iff interrupted (ExecResult)
            return self._end(t, ex.interrupt_cause.task_outcome)  # the executor already sent StopMove
        return sr
