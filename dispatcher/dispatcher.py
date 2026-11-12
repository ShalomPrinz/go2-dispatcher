"""Dispatcher loop: plan -> validate -> execute -> feed back (dispatcher/docs/loop-and-context.md)."""

from __future__ import annotations

import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, NoReturn

from . import prompts
from .bounds import cut_message, precheck
from .budget import MotionBudget
from .config import Config
from .context import ContextInput, Feedback, RequestReason, ReturnReason, build_user_message, schema_retry_message
from .executor import STDERR_TAIL_CHARS, SkillExecutor
from .llm import LLMResult, PlanCheck, PlannerClient, check_reply
from .models import (
    KILLED_OUTCOMES,
    BusyError,
    LLMInterrupted,
    LLMUnavailable,
    Plan,
    Posture,
    PreconditionFailure,
    RobotServiceLostError,
    RobotState,
    StartState,
    StateReadError,
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
from .runlog import NullLog, RunLogFactory, SessionInfo, TaskLog, TaskObserver, TeeLog, notify, stream_path
from .verification import verify

__all__ = ["Dispatcher"]

EXCEPTION_WHERE = "run_task"

Phase = Literal["idle", "starting", "llm_call", "step", "between", "ending"]


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
    state: RobotState | None = None  # last known state (dispatcher/docs/loop-and-context.md)

    @property
    def posture(self) -> Posture:
        return self.state.posture if self.state is not None else "unknown"


@dataclass(frozen=True)
class _Ending:
    """How a task ended: the arguments ``Dispatcher._end`` is called with (dispatcher/docs/loop-and-context.md)."""

    outcome: TaskOutcomeCode
    message: str | None = None
    detail: str = ""
    exception_type: str = "Exception"
    stop_move: StopMoveResult | None = None
    precondition: PreconditionFailure | None = None  # PRECONDITION_FAILED


class _TaskEnded(Exception):
    """Raised inside the loop to end the task; ``run_task`` handles it once (dispatcher/docs/loop-and-context.md)."""

    def __init__(self, ending: _Ending):
        super().__init__(ending.outcome)
        self.ending = ending


class Dispatcher:
    def __init__(
        self,
        cfg: Config,
        registry: Registry,
        planner: PlannerClient,
        executor: SkillExecutor,
        *,
        clock: Callable[[], float] = time.monotonic,
        observer: TaskObserver | None = None,
    ):
        self.cfg = cfg
        self.registry = registry
        self.planner = planner
        self.executor = executor
        # the log copies the surface the planner sends (dispatcher/docs/run-log.md)
        self.runlog_factory = RunLogFactory(
            cfg.run_log_dir, uuid.uuid4().hex, SessionInfo.collect(cfg, planner.surface)
        )
        self.registry_hash = planner.surface.registry_hash
        self.clock = clock
        self.observer = observer  # sees every record, e.g. the CLI console trace (dispatcher/docs/run-log.md)
        self.previous: TaskSummary | None = None

        self._task_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._phase: Phase = "idle"
        self._stop_event = threading.Event()
        self._log: TaskLog | None = None  # the plain log, without the observer (dispatcher/docs/run-log.md)
        self._pending_stop: tuple[str, Phase] | None = None  # a stop before the log opened, logged once it has

    # --- public interface (dispatcher/docs/loop-and-context.md) ------------------------------------------------------

    def is_busy(self) -> bool:
        return self._task_lock.locked()

    def request_stop(self, source: str) -> Literal["stopping", "idle"]:
        with self._state_lock:
            if self._phase in ("idle", "ending"):
                return "idle"
            self._stop_event.set()
            during = self._phase
            logged = self._log is not None
            if self._log is not None:
                self._log.stop_requested(source, during)
            elif self._pending_stop is None:
                self._pending_stop = (source, during)
        self.executor.kill_current(StopCause.OPERATOR)
        if logged:  # the observer runs after the kill and outside the lock (dispatcher/docs/run-log.md)
            notify(self.observer, lambda o: o.stop_requested(source, during))
        return "stopping"

    def shutdown(self, wait_s: float) -> None:
        """End a running task, then close the executor (the vision and robot services); safe to call twice."""
        try:
            self._end_running_task(wait_s)
        finally:
            self.executor.close()

    def _end_running_task(self, wait_s: float) -> None:
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
            logged = self._log is not None
            if self._log is not None:
                self._log.stop_move(smr)
        if logged:
            notify(self.observer, lambda o: o.stop_move(smr))

    def run_task(self, task: str, *, source: str, sender_id: str | None = None) -> TaskOutcome:
        if not task.strip():
            raise ValueError("empty task")
        if not self._task_lock.acquire(blocking=False):
            raise BusyError("a task is running")
        try:
            self._set_phase("starting")  # a stop from here on is honoured (dispatcher/docs/loop-and-context.md)
            t = self._new_task(task, source, sender_id)
            stream = None
            try:
                start = self._read_start_state(t)
                self._open_log(t, start)
                try:
                    if start.error is not None:  # no plan without a fresh state (dispatcher/docs/loop-and-context.md)
                        self._state_unavailable(start.error)
                    if t.log.path is not None:
                        stream = self.executor.state_stream(stream_path(t.log.path), t.t_start, self.observer)
                    self._loop(t)
                except _TaskEnded as ended:  # the one place a task ends (dispatcher/docs/loop-and-context.md)
                    end = ended.ending
                    return self._end(
                        t,
                        end.outcome,
                        message=end.message,
                        detail=end.detail,
                        exception_type=end.exception_type,
                        stop_move=end.stop_move,
                        precondition=end.precondition,
                    )
            except Exception as e:  # noqa: BLE001 - every unhandled error ends the task (dispatcher/docs/loop-and-context.md)
                return self._internal_error(t, e)
            finally:
                if stream is not None:
                    stream.close()
        finally:
            with self._state_lock:
                self._phase = "idle"
                self._stop_event.clear()
                self._pending_stop = None
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
            log=self._observed(NullLog()),
        )

    def _observed(self, log: TaskLog) -> TaskLog:
        return log if self.observer is None else TeeLog(log, self.observer)

    def _read_start_state(self, t: _Task) -> StartState:
        """Read the state for the task's first plan through the robot service (dispatcher/docs/loop-and-context.md)."""
        try:
            t.state = self.executor.fresh_state()
        except StateReadError as e:
            return StartState(age_ms=None, error=str(e))
        return StartState(age_ms=t.state.age_ms)

    def _open_log(self, t: _Task, start_state: StartState) -> None:
        plain = self.runlog_factory.open(t.run_id, t.t_start, clock=self.clock)
        log = self._observed(plain)
        t.log = log
        log.task_start(t.task, t.source, t.sender_id, self.previous, t.posture, start_state)
        with self._state_lock:  # task_start stays the first record, even with a stop during the start
            self._log = plain
            self._phase = "between"
            pending, self._pending_stop = self._pending_stop, None
        if pending is not None:
            log.stop_requested(*pending)

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
        if sr.is_failure:
            t.failures += 1
            t.last_failure = sr
        if sr.dispatch is None:
            t.rejections += 1
        t.log.step_result(sr, budget=t.budget, failures=t.failures, posture=t.posture)
        if stop is not None:
            t.log.stop_move(stop)

    def _apply_state(self, t: _Task, sr: StepResult | None, stop: StopMoveResult | None) -> None:
        """Update the last known state and the StopMove flag (dispatcher/docs/loop-and-context.md)."""
        step_state = sr.response.state_after if sr is not None and sr.response is not None else None
        for state in (step_state, stop.state_after if stop else None):
            if state is not None:
                t.state = state  # the step's state_after wins over the stop's
                break
        else:
            if sr is not None and sr.outcome in KILLED_OUTCOMES:
                t.state = None
        if stop is not None and not stop.ok:
            t.stop_move_failed = True

    def _context(self, t: _Task) -> str:
        loop = self.cfg.loop
        return build_user_message(
            ContextInput(
                task=t.task,
                state=t.state,
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
    ) -> LLMResult:
        """One planner call. Raises ``_TaskEnded`` if the call ended the task
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
            self._stop(t, e.cause)
        except LLMUnavailable as e:
            self._set_phase("between")
            t.log.llm_error(call_index, e.detail)
            raise _TaskEnded(_Ending("LLM_ERROR", detail=e.detail)) from None
        except BaseException:
            self._set_phase("between")
            raise
        self._set_phase("between")

        t.llm_calls += 1
        for k, v in _numeric_usage(res.usage).items():
            t.usage_totals[k] = t.usage_totals.get(k, 0) + v
        t.log.llm_response(call_index, res)
        self._check_interrupts(t)
        return res

    def _log_invalid(self, t: _Task, res: LLMResult, check: PlanCheck) -> None:
        if check.rejection_kind == "horizon":
            t.horizon_rejections += 1
        t.log.plan_invalid(t.llm_calls, res.tool_input, check, self.cfg.loop.planning_horizon)

    # --- interrupts and end ----------------------------------------------------------------------

    def _check_interrupts(self, t: _Task) -> None:
        if self._stop_event.is_set():
            self._stop(t, StopCause.OPERATOR)
        if self._remaining_s(t) <= 0:
            self._stop(t, StopCause.TASK_TIME_LIMIT)

    def _stop(self, t: _Task, cause: StopCause) -> NoReturn:
        """End the task for an interrupt seen by the loop: send StopMove, then raise."""
        self._set_phase("ending")
        smr = self.executor.stop_move(cause)
        raise _TaskEnded(_Ending(cause.task_outcome, stop_move=smr)) from None

    def _state_unavailable(self, error: str) -> NoReturn:
        """End the task for a failed task-start or pre-step read: the state is unknown, so send StopMove, then
        raise."""
        self._set_phase("ending")
        smr = self.executor.stop_move(StopCause.STATE_UNAVAILABLE)
        raise _TaskEnded(_Ending("STATE_UNAVAILABLE", detail=error, stop_move=smr)) from None

    def _service_lost(self) -> NoReturn:
        """End the task for a robot service found dead before a step: the fallback stop, as after a loss during a
        step (dispatcher/docs/loop-and-context.md)."""
        self._set_phase("ending")
        smr = self.executor.stop_move(StopCause.ROBOT_SERVICE_LOST)
        detail = prompts.SERVICE_LOST_STOP_CONFIRMED if smr.ok else prompts.SERVICE_LOST_STOP_FAILED
        raise _TaskEnded(_Ending("ROBOT_SERVICE_LOST", detail=detail, stop_move=smr)) from None

    def _end(
        self,
        t: _Task,
        outcome: TaskOutcomeCode,
        *,
        message: str | None = None,
        detail: str = "",
        exception_type: str = "Exception",
        stop_move: StopMoveResult | None = None,
        precondition: PreconditionFailure | None = None,
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
            precondition=precondition,
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
            final_posture=t.posture,
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
                    stop_path=None,
                    duration_ms=(self.clock() - t0) * 1000.0,
                    stderr_tail=f"{type(se).__name__}: {se}"[-STDERR_TAIL_CHARS:],
                )
        finally:
            # also runs if a BaseException (e.g. KeyboardInterrupt) arrives above; it then propagates
            outcome = self._end(t, "INTERNAL_ERROR", stop_move=smr, exception_type=type(e).__name__)
        return outcome

    # --- the loop (dispatcher/docs/loop-and-context.md) --------------------------------------------------------------

    def _loop(self, t: _Task) -> NoReturn:
        """Runs until the task ends by raising ``_TaskEnded``."""
        while True:
            self._check_interrupts(t)
            plan = self._get_valid_plan(t)
            feedback = self._execute_plan(t, plan)
            if feedback.return_reason == "failure" and t.failures >= self.cfg.loop.max_failures:
                raise _TaskEnded(_Ending("FAILURE_BUDGET_EXHAUSTED"))
            t.feedback = feedback

    def _get_valid_plan(self, t: _Task) -> Plan:
        """The request, then at most one schema retry (dispatcher/docs/llm.md). Returns a valid plan, or
        raises ``_TaskEnded``."""
        request = self._context(t)
        return_reason: RequestReason = t.feedback.return_reason
        retry_of: ReturnReason | None = None
        while True:
            if t.llm_calls >= self.cfg.loop.max_llm_calls:
                raise _TaskEnded(_Ending("CALL_BUDGET_EXHAUSTED"))
            res = self._call(t, request, return_reason, retry_of)
            check = check_reply(res, self.cfg.loop.planning_horizon)
            if check.plan is not None:
                return check.plan
            self._log_invalid(t, res, check)
            if retry_of is not None:
                raise _TaskEnded(_Ending("LLM_INVALID"))
            request, return_reason, retry_of = (  # rebuilt so the Budget shows the call just made
                schema_retry_message(self._context(t), check.errors),
                "schema_retry",
                t.feedback.return_reason,
            )

    def _execute_plan(self, t: _Task, plan: Plan) -> Feedback:
        """Run a valid plan up to ``stop_at``. Returns the feedback for the next call, or raises ``_TaskEnded``."""
        stop_at = plan.replan_after or len(plan.steps)
        t.log.plan(t.llm_calls, plan, stop_at)
        if plan.status == "DONE":
            raise _TaskEnded(_Ending("DONE", message=plan.message))
        if plan.status == "ABORT":
            raise _TaskEnded(_Ending("ABORTED", message=plan.message))
        if plan.status == "ASK":  # ends the task like ABORT; the answer is a new task (loop-and-context.md)
            raise _TaskEnded(_Ending("ASKED", message=plan.message))

        pre = precheck(plan, stop_at, self.registry, t.budget, call_index=t.llm_calls)
        if pre.rejection is not None:
            self._record(t, pre.rejection)
            return Feedback.failure(plan.steps, pre.rejection, t.failures, ran=0)

        for i, step in enumerate(plan.steps[:stop_at], start=1):
            self._check_interrupts(t)
            ref = StepRef(call_index=t.llm_calls, plan_step=i, skill=step.skill, params=pre.filled[i - 1])
            self._check_precondition(t, ref)
            sr = self._run_step(t, ref)
            if sr.is_failure:  # a failing outcome or a failed verdict (dispatcher/docs/loop-and-context.md)
                return Feedback.failure(plan.steps, sr, t.failures, ran=i)
        if stop_at < len(plan.steps):
            return Feedback.checkpoint(plan.steps, stop_at)
        return Feedback.plan_complete()

    def _check_precondition(self, t: _Task, ref: StepRef) -> None:
        """Read a fresh state and check the step's ``requires`` on it; raises ``_TaskEnded`` if the read fails
        or the check does not hold (dispatcher/docs/loop-and-context.md)."""
        try:
            t.state = self.executor.fresh_state(start=False)  # a step never starts the robot service
        except RobotServiceLostError:
            t.state = None
            self._service_lost()
        except StateReadError as e:
            t.state = None
            self._state_unavailable(str(e))
        requires = self.registry[ref.skill].requires
        if requires is not None and t.state.posture not in requires.posture:
            failure = PreconditionFailure(ref=ref, requires=list(requires.posture), state=t.state)
            t.log.precondition_failed(failure)
            raise _TaskEnded(_Ending("PRECONDITION_FAILED", precondition=failure))

    def _run_step(self, t: _Task, ref: StepRef) -> StepResult:
        """Dispatch the step ``ref`` names. Returns its recorded result, or raises ``_TaskEnded``."""
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

        exec_fields = {k: v for k, v in ex if k not in ("interrupt_cause", "stop_move", "service_lost")}  # 1:1
        exec_fields["error_message"] = cut_message(ex.error_message) if ex.error_message else None
        check = verify(desc.expect, params, ex.response, ok=ex.outcome == "ok")
        sr = StepResult(ref=ref, dispatch=dispatch, verification=check.verdict, faults=check.faults, **exec_fields)
        self._record(t, sr, ex.stop_move)

        if sr.outcome == "interrupted":
            assert ex.interrupt_cause is not None  # set iff interrupted (ExecResult)
            raise _TaskEnded(_Ending(ex.interrupt_cause.task_outcome))  # the executor already sent StopMove
        if ex.service_lost:  # every loss ends the task, with no further LLM call (dispatcher/docs/loop-and-context.md)
            confirmed = ex.stop_move is not None and ex.stop_move.ok
            detail = prompts.SERVICE_LOST_STOP_CONFIRMED if confirmed else prompts.SERVICE_LOST_STOP_FAILED
            raise _TaskEnded(_Ending("ROBOT_SERVICE_LOST", detail=detail))
        if ex.stop_move is not None and not ex.stop_move.ok:  # no LLM call without a confirmed stop (docs/safety.md)
            raise _TaskEnded(_Ending("STOP_FAILED"))
        return sr
