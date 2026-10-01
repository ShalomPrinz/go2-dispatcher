"""Dispatcher loop: plan -> validate -> execute -> feed back (docs/loop-and-context.md)."""

from __future__ import annotations

import importlib.metadata
import platform
import subprocess
import sys
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from . import prompts
from .bounds import cut_message, precheck
from .budget import MotionBudget
from .clock import Clock, MonotonicClock
from .config import Config
from .context import ContextInput, build_user_message, schema_retry_message
from .executor import STDERR_TAIL_CHARS, Executor
from .llm import LLMResult, PlannerClient, plan_tool_schema
from .models import (
    FAILURE_OUTCOMES,
    BusyError,
    LLMInterrupted,
    LLMUnavailable,
    MotionCostModel,
    PlanStep,
    StepResult,
    StopMoveResult,
    TaskOutcome,
    TaskOutcomeCode,
    TaskSummary,
)
from .registry import Registry, registry_hash
from .runlog import RunLog, RunLogFactory

__all__ = ["Dispatcher", "GIT_TIMEOUT_S", "VERSION_PACKAGES"]

GIT_TIMEOUT_S = 5.0                          # best-effort `git rev-parse HEAD` at startup
VERSION_PACKAGES = ("anthropic", "pydantic", "go2_dispatcher")
KILLED_OUTCOMES = frozenset({"timeout", "interrupted"})
STOP_CAUSES = frozenset({"operator", "shutdown"})   # interrupted -> STOPPED; else TIME_LIMIT
EXCEPTION_WHERE = "run_task"

Phase = Literal["idle", "llm_call", "step", "between", "ending"]


# --- startup facts for task_start ------------------------------------------------------


def _versions() -> dict[str, str | None]:
    out: dict[str, str | None] = {"python": platform.python_version()}
    for pkg in VERSION_PACKAGES:
        try:
            out[pkg] = importlib.metadata.version(pkg)
        except importlib.metadata.PackageNotFoundError:
            out[pkg] = None
    return out


def _git_commit(base_dir: Path) -> str | None:
    try:
        proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=base_dir, capture_output=True,
                              text=True, timeout=GIT_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _numeric_usage(usage: dict) -> dict[str, float]:
    return {k: v for k, v in usage.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)}


class _NullLog:
    """Stands in until the task's log file is open (or if opening it failed)."""

    path = ""

    def write(self, type: str, /, **payload: Any) -> None:
        pass

    def close(self) -> None:
        pass


# --- per-task state (docs/loop-and-context.md) --------------------------------------------------------------


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
    log: RunLog | _NullLog = field(default_factory=_NullLog)
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
    def __init__(self, cfg: Config, registry: Registry, planner: PlannerClient,
                 executor: Executor, runlog_factory: RunLogFactory, *,
                 clock: Clock = MonotonicClock(), initial_posture: str = "unknown"):
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
        self._log: RunLog | _NullLog | None = None

        horizon = cfg.loop.planning_horizon
        self._catalog_text = registry.catalog_text()
        self._system = prompts.system_blocks(horizon, self._catalog_text)
        self._tool_schema = plan_tool_schema(horizon)
        self.registry_hash = registry_hash(self._system[0], self._catalog_text, self._tool_schema)
        self._versions = _versions()
        self._git_commit = _git_commit(cfg.base_dir)

    # --- public interface (docs/loop-and-context.md) -------------------------------------------------------

    def is_busy(self) -> bool:
        return self._task_lock.locked()

    def request_stop(self, source: str) -> Literal["stopping", "idle"]:
        with self._state_lock:
            if self._phase in ("idle", "ending"):
                return "idle"
            self._stop_event.set()
            if self._log is not None:
                self._log.write("stop_requested", source=source, during=self._phase)
        self.executor.kill_current("operator")
        return "stopping"

    def shutdown(self, wait_s: float) -> None:
        if not self.is_busy():
            return
        self.request_stop("shutdown")
        acquired = (self._task_lock.acquire(timeout=wait_s) if wait_s > 0
                    else self._task_lock.acquire(blocking=False))
        if acquired:
            self._task_lock.release()
            return
        self.executor.kill_current("shutdown")
        smr = self.executor.stop_move("shutdown")
        with self._state_lock:
            if self._log is not None:
                self._log.write("stop_move", **smr.model_dump(mode="json"))

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
            except Exception as e:  # noqa: BLE001 - every unhandled error ends the task (docs/loop-and-context.md)
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
        now = self.clock.now()
        mb = self.cfg.motion_budget
        return _Task(run_id=uuid.uuid4().hex, task=task, source=source, sender_id=sender_id,
                     t_start=now, ts_start=datetime.now().astimezone().isoformat(),
                     deadline=now + self.cfg.loop.task_time_limit_s,
                     budget=MotionBudget(mb.max_distance_m, mb.max_rotation_deg))

    def _open_log(self, t: _Task) -> None:
        log = self.runlog_factory.open(t.run_id, t.t_start, clock=self.clock)
        with self._state_lock:
            t.log = log
            self._log = log
            self._phase = "between"
        log.write(
            "task_start",
            task=t.task, source=t.source, sender_id=t.sender_id,
            condition=self.cfg.run.condition,
            config=self.cfg.model_dump(mode="json"),
            registry_hash=self.registry_hash,
            system_text=self._system[0], catalog_text=self._catalog_text,
            tool_schema=self._tool_schema,
            skills=self.registry.names(),
            previous_task=self.previous.model_dump(mode="json") if self.previous else None,
            posture=self.posture,
            versions=self._versions, git_commit=self._git_commit,
        )

    # --- helpers ------------------------------------------------------------------------------

    def _set_phase(self, phase: Phase) -> None:
        with self._state_lock:
            self._phase = phase

    def _remaining_s(self, t: _Task) -> float:
        return t.deadline - self.clock.now()

    def _budget_used(self, t: _Task) -> dict[str, float]:
        return {"distance_m": t.budget.used_distance_m, "rotation_deg": t.budget.used_rotation_deg}

    def _record(self, t: _Task, sr: StepResult) -> None:
        """Append a step, update posture and the StopMove flag, log it (and its StopMove)."""
        t.steps.append(sr)
        if sr.index is not None:
            self._update_posture_from_step(sr)
        if sr.stop_move is not None and not sr.stop_move.ok:
            t.stop_move_failed = True
        if sr.outcome in FAILURE_OUTCOMES:
            t.failures += 1
            t.last_failure = sr
        if sr.index is None:
            t.rejections += 1
        t.log.write("step_result", **sr.model_dump(mode="json"),
                    budget_used=self._budget_used(t), failures=t.failures, posture=self.posture)
        if sr.stop_move is not None:
            t.log.write("stop_move", **sr.stop_move.model_dump(mode="json"))

    def _update_posture_from_step(self, sr: StepResult) -> None:
        """Last known posture from the step's state_after (docs/loop-and-context.md)."""
        if sr.response is not None and sr.response.state_after is not None:
            self.posture = sr.response.state_after.posture
        elif (sr.stop_move is not None and sr.stop_move.response is not None
              and sr.stop_move.response.state_after is not None):
            self.posture = sr.stop_move.response.state_after.posture
        elif sr.outcome in KILLED_OUTCOMES:
            self.posture = "unknown"

    def _context(self, t: _Task) -> str:
        loop = self.cfg.loop
        return build_user_message(ContextInput(
            task=t.task, posture=self.posture, budget=t.budget,
            failures=t.failures, max_failures=loop.max_failures,
            llm_calls=t.llm_calls, max_llm_calls=loop.max_llm_calls,
            history_k=loop.context_history_k, return_reason=t.return_reason,
            steps=list(t.steps), remaining=list(t.remaining), remaining_tag=t.remaining_tag,
            notice_args=dict(t.notice_args), previous=self.previous,
        ), self.registry)

    # --- LLM call ------------------------------------------------------------------------------

    def _call(self, t: _Task, user: str, return_reason: str, retry_of: str | None = None
              ) -> LLMResult | TaskOutcome:
        """One planner call. Returns the result, or the task outcome if the call ended it
        (interrupted, unavailable, or a stop/deadline that arrived during the call)."""
        call_index = t.llm_calls + 1
        self._set_phase("llm_call")
        t.log.write("llm_request", call_index=call_index, return_reason=return_reason,
                    retry_of=retry_of, user_text=user)
        try:
            res = self.planner.plan(
                system=list(self._system), user=user, tool_schema=self._tool_schema,
                call_index=call_index, remaining_s=lambda: self._remaining_s(t),
                stop_event=self._stop_event,
                on_infra_retry=lambda rec: t.log.write("llm_retry", **rec))
        except LLMInterrupted as e:
            self._set_phase("between")
            t.log.write("llm_interrupted", call_index=call_index, cause=e.cause)
            return self._end(t, "STOPPED" if e.cause == "operator" else "TIME_LIMIT_EXCEEDED",
                             stop_move=True)
        except LLMUnavailable as e:
            self._set_phase("between")
            t.log.write("llm_error", call_index=call_index, detail=e.detail)
            return self._end(t, "LLM_ERROR", detail=e.detail)
        except BaseException:
            self._set_phase("between")
            raise
        self._set_phase("between")

        t.llm_calls += 1
        for k, v in _numeric_usage(res.usage).items():
            t.usage_totals[k] = t.usage_totals.get(k, 0) + v
        t.log.write("llm_response", call_index=call_index, latency_ms=res.latency_ms,
                    total_ms=res.total_ms, attempts=res.attempts, stop_reason=res.stop_reason,
                    usage=res.usage, content=res.content, response_id=res.response_id,
                    request_id=res.request_id)
        if (o := self._check_interrupts(t)) is not None:
            return o
        return res

    def _log_invalid(self, t: _Task, res: LLMResult) -> None:
        call_index = t.llm_calls
        if res.horizon_exceeded:
            t.horizon_rejections += 1
            raw_steps = res.tool_input.get("steps") if isinstance(res.tool_input, dict) else None
            t.log.write("horizon_rejection", call_index=call_index, tool_input=res.tool_input,
                        steps_in_plan=len(raw_steps) if isinstance(raw_steps, list) else None,
                        horizon=self.cfg.loop.planning_horizon, errors=res.errors)
        else:
            t.log.write("plan_invalid", call_index=call_index, tool_input=res.tool_input,
                        rejection_kind=res.rejection_kind, errors=res.errors)

    # --- interrupts and end ----------------------------------------------------------------------

    def _check_interrupts(self, t: _Task) -> TaskOutcome | None:
        if self._stop_event.is_set():
            return self._end(t, "STOPPED", stop_move=True)
        if self._remaining_s(t) <= 0:
            return self._end(t, "TIME_LIMIT_EXCEEDED", stop_move=True)
        return None

    def _operator_args(self, t: _Task, outcome: TaskOutcomeCode, message: str | None,
                       extra: dict) -> dict:
        loop = self.cfg.loop
        if outcome in ("DONE", "ABORTED"):
            return {"message": message or ""}
        if outcome == "TIME_LIMIT_EXCEEDED":
            return {"limit": loop.task_time_limit_s}
        if outcome == "FAILURE_BUDGET_EXHAUSTED":
            lf = t.last_failure
            return {"n": t.failures, "skill": lf.skill if lf else "",
                    "error_message": (lf.error_message or lf.outcome) if lf else ""}
        if outcome == "CALL_BUDGET_EXHAUSTED":
            return {"n": loop.max_llm_calls}
        if outcome == "LLM_ERROR":
            return {"detail": extra.get("detail", "")}
        if outcome == "INTERNAL_ERROR":
            return {"exception_type": extra.get("exception_type", "Exception"),
                    "run_id": t.run_id}
        return {}

    def _apply_stop_move(self, t: _Task, smr: StopMoveResult) -> None:
        t.log.write("stop_move", **smr.model_dump(mode="json"))
        if smr.response is not None and smr.response.state_after is not None:
            self.posture = smr.response.state_after.posture
        if not smr.ok:
            t.stop_move_failed = True

    def _end(self, t: _Task, outcome: TaskOutcomeCode, *, message: str | None = None,
             stop_move: bool = False, stop_move_result: StopMoveResult | None = None,
             **extra: Any) -> TaskOutcome:
        self._set_phase("ending")
        if stop_move:
            reason = "operator" if outcome == "STOPPED" else "task_time_limit"
            self._apply_stop_move(t, self.executor.stop_move(reason))
        if stop_move_result is not None:
            self._apply_stop_move(t, stop_move_result)

        text = prompts.operator_message(outcome, stop_move_failed=t.stop_move_failed,
                                        **self._operator_args(t, outcome, message, extra))
        duration_ms = (self.clock.now() - t.t_start) * 1000.0
        dispatched = [s for s in t.steps if s.index is not None]
        result = TaskOutcome(
            run_id=t.run_id, task=t.task, outcome=outcome, message=text, steps=list(t.steps),
            llm_calls=t.llm_calls, failures=t.failures, stop_move_failed=t.stop_move_failed,
            duration_ms=duration_ms, final_posture=self.posture, log_path=str(t.log.path))
        try:
            t.log.write("task_end", outcome=outcome, message=text, llm_calls=t.llm_calls,
                        failures=t.failures, steps_recorded=len(t.steps),
                        steps_dispatched=len(dispatched), rejections=t.rejections,
                        horizon_rejections=t.horizon_rejections, usage_totals=t.usage_totals,
                        budget_used=self._budget_used(t), stop_move_failed=t.stop_move_failed,
                        final_posture=self.posture, duration_ms=duration_ms)
        except Exception as e:  # noqa: BLE001 - the index row must still be written (docs/loop-and-context.md)
            print(f"go2-dispatcher: failed to write task_end for run {t.run_id}: "
                  f"{type(e).__name__}: {e}", file=sys.stderr)
        finally:
            if isinstance(t.log, RunLog):
                self.runlog_factory.append_index({
                    "run_id": t.run_id, "file": t.log.path.name, "ts_start": t.ts_start,
                    "task": t.task, "source": t.source, "outcome": outcome,
                    "condition": self.cfg.run.condition, "backend": self.cfg.robot.backend,
                    "model": self.cfg.llm.model, "thinking": self.cfg.llm.thinking,
                    "planning_horizon": self.cfg.loop.planning_horizon,
                    "max_llm_calls": self.cfg.loop.max_llm_calls,
                    "registry_hash": self.registry_hash, "llm_calls": t.llm_calls,
                    "failures": t.failures, "steps_dispatched": len(dispatched),
                    "input_tokens": t.usage_totals.get("input_tokens", 0),
                    "output_tokens": t.usage_totals.get("output_tokens", 0),
                    "duration_ms": duration_ms,
                })
        self.previous = TaskSummary(task=t.task, outcome=outcome, message=text,
                                    last_step=dispatched[-1] if dispatched else None)
        return result

    def _internal_error(self, t: _Task, e: Exception) -> TaskOutcome:
        """Nothing may escape before task_end and the index row are written (docs/loop-and-context.md)."""
        smr: StopMoveResult | None = None
        try:
            try:
                t.log.write("exception", where=EXCEPTION_WHERE, exception_type=type(e).__name__,
                            message=str(e), traceback=traceback.format_exc())
            except Exception:  # noqa: BLE001
                pass
            try:
                self.executor.kill_current("shutdown")
            except Exception:  # noqa: BLE001
                pass
            t0 = self.clock.now()
            try:
                smr = self.executor.stop_move("internal_error")
            except Exception as se:  # noqa: BLE001 - Executor.stop_move never raises; fakes might
                smr = StopMoveResult(ok=False, reason="internal_error",
                                     duration_ms=(self.clock.now() - t0) * 1000.0,
                                     stderr_tail=f"{type(se).__name__}: {se}"[-STDERR_TAIL_CHARS:])
        finally:
            # also runs if a BaseException (e.g. KeyboardInterrupt) arrives above; it then propagates
            outcome = self._end(t, "INTERNAL_ERROR", stop_move_result=smr,
                                exception_type=type(e).__name__)
        return outcome

    # --- the loop (docs/loop-and-context.md) ----------------------------------------------------------------------

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
                res = self._call(t, schema_retry_message(user, res.errors), "schema_retry",
                                 retry_of=t.return_reason)
                if isinstance(res, TaskOutcome):
                    return res
                if res.errors:
                    self._log_invalid(t, res)
                    return self._end(t, "LLM_INVALID")

            plan = res.plan
            stop_at = plan.replan_after or len(plan.steps)
            t.log.write("plan", call_index=t.llm_calls, plan=plan.model_dump(mode="json"),
                        stop_at=stop_at)
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
                t.notice_args = {"n": rej.plan_step, "skill": rej.skill,
                                 "outcome": rej.outcome, "f": t.failures}
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

    def _run_step(self, t: _Task, steps: list[PlanStep], i: int, step: PlanStep,
                  params: dict) -> TaskOutcome | bool:
        """Dispatch plan step ``i``. Returns the task outcome if the task ended, else
        whether the step failed."""
        desc = self.registry.get(step.skill)
        cost = desc.policy.motion_cost(params)
        timeout = desc.policy.timeout_s(params)
        t.dispatched_count += 1
        fault = None
        if self.cfg.robot.backend == "stub":
            fault = next((f.kind for f in self.cfg.stub.faults
                          if f.step == t.dispatched_count), None)
        t.budget.charge(cost)
        cost_model = MotionCostModel(distance_m=cost.distance_m, rotation_deg=cost.rotation_deg)

        self._set_phase("step")
        t.log.write("step_start", index=t.dispatched_count, call_index=t.llm_calls,
                    plan_step=i, skill=step.skill, params=params, timeout_s=timeout,
                    motion_cost=cost_model.model_dump(), fault=fault)
        try:
            ex = self.executor.run(desc, params, fault=fault, timeout_s=timeout,
                                   remaining_task_s=self._remaining_s(t),
                                   stop_event=self._stop_event)
        finally:
            self._set_phase("between")

        sr = StepResult(
            index=t.dispatched_count, call_index=t.llm_calls, plan_step=i, skill=step.skill,
            params=params, outcome=ex.outcome, error_code=ex.error_code,
            error_message=cut_message(ex.error_message) if ex.error_message else None,
            response=ex.response, duration_ms=ex.duration_ms, timeout_s=timeout,
            motion_cost=cost_model, fault=fault, exit_code=ex.exit_code, pid=ex.pid,
            stderr_tail=ex.stderr_tail, stop_move=ex.stop_move)
        self._record(t, sr)

        if sr.outcome == "interrupted":
            # StopMove was already sent by the executor
            return self._end(t, "STOPPED" if ex.interrupt_cause in STOP_CAUSES
                             else "TIME_LIMIT_EXCEEDED")
        if sr.outcome in FAILURE_OUTCOMES:
            t.remaining = [(j, s) for j, s in enumerate(steps, start=1) if j > i]
            t.remaining_tag = "abandoned"
            t.notice_args = {"n": i, "skill": sr.skill, "outcome": sr.outcome, "f": t.failures}
            return True
        return False
