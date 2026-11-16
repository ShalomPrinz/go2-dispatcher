"""The record protocols the loop writes to and their wrappers: no file, or file plus observer
(dispatcher/docs/run-log.md)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from contract.constants import Link

    from ..context import RequestReason, ReturnReason
    from ..llm.client import InfraRetry, LLMResult
    from ..models import (
        Posture,
        PreconditionFailure,
        RobotState,
        StartState,
        StepDispatch,
        StepRef,
        StepResult,
        StopMoveResult,
        TaskOutcome,
        TaskSummary,
    )
    from ..planning.budget import MotionBudget
    from ..planning.plan import Plan
    from ..planning.validation import PlanCheck


class TaskRecords(Protocol):
    """One method per record type, called with the same arguments as ``RunLog`` (dispatcher/docs/run-log.md)."""

    def task_start(
        self,
        task: str,
        source: str,
        sender_id: str | None,
        previous: TaskSummary | None,
        posture: Posture,
        start_state: StartState,
    ) -> None: ...

    def llm_request(
        self, call_index: int, return_reason: RequestReason, retry_of: ReturnReason | None, user_text: str
    ) -> None: ...

    def llm_retry(self, retry: InfraRetry) -> None: ...

    def llm_response(self, call_index: int, res: LLMResult) -> None: ...

    def llm_error(self, call_index: int, detail: str) -> None: ...

    def llm_interrupted(self, call_index: int, cause: str) -> None: ...

    def plan(self, call_index: int, plan: Plan, stop_at: int) -> None: ...

    def plan_invalid(self, call_index: int, tool_input: Any, check: PlanCheck, horizon: int) -> None: ...

    def step_start(self, ref: StepRef, dispatch: StepDispatch) -> None: ...

    def step_result(self, sr: StepResult, *, budget: MotionBudget, failures: int, posture: Posture) -> None: ...

    def precondition_failed(self, failure: PreconditionFailure) -> None: ...

    def stop_requested(self, source: str, during: str) -> None: ...

    def stop_move(self, smr: StopMoveResult) -> None: ...

    def exception(self, where: str, e: BaseException) -> None: ...

    def task_end(
        self, result: TaskOutcome, *, rejections: int, horizon_rejections: int, usage_totals: dict, budget: MotionBudget
    ) -> None: ...


class TaskObserver(TaskRecords, Protocol):
    """The records through ``TeeLog``, plus the task's state stream: each downsampled sample and each link change,
    called on the stream thread. Observers such as the CLI console trace (dispatcher/docs/run-log.md)."""

    def robot_sample(self, state: RobotState, link: Link, rx_mono: float | None) -> None: ...

    def robot_link(self, link: Link, rx_mono: float | None) -> None: ...


class TaskLog(TaskRecords, Protocol):
    """What the loop writes for one task: ``RunLog`` once the file is open, ``NullLog`` before that or if opening
    failed, either wrapped in a ``TeeLog`` when an observer is set. ``path`` is None when there is no file, so no
    index row is written (dispatcher/docs/run-log.md)."""

    @property
    def path(self) -> Path | None: ...

    def close(self) -> None: ...


class NullLog:
    """The ``TaskLog`` with no file: every record is dropped and ``path`` is None."""

    @property
    def path(self) -> None:
        return None

    def close(self) -> None:
        pass

    def task_start(
        self,
        task: str,
        source: str,
        sender_id: str | None,
        previous: TaskSummary | None,
        posture: Posture,
        start_state: StartState,
    ) -> None:
        pass

    def llm_request(
        self, call_index: int, return_reason: RequestReason, retry_of: ReturnReason | None, user_text: str
    ) -> None:
        pass

    def llm_retry(self, retry: InfraRetry) -> None:
        pass

    def llm_response(self, call_index: int, res: LLMResult) -> None:
        pass

    def llm_error(self, call_index: int, detail: str) -> None:
        pass

    def llm_interrupted(self, call_index: int, cause: str) -> None:
        pass

    def plan(self, call_index: int, plan: Plan, stop_at: int) -> None:
        pass

    def plan_invalid(self, call_index: int, tool_input: Any, check: PlanCheck, horizon: int) -> None:
        pass

    def step_start(self, ref: StepRef, dispatch: StepDispatch) -> None:
        pass

    def step_result(self, sr: StepResult, *, budget: MotionBudget, failures: int, posture: Posture) -> None:
        pass

    def precondition_failed(self, failure: PreconditionFailure) -> None:
        pass

    def stop_requested(self, source: str, during: str) -> None:
        pass

    def stop_move(self, smr: StopMoveResult) -> None:
        pass

    def exception(self, where: str, e: BaseException) -> None:
        pass

    def task_end(
        self, result: TaskOutcome, *, rejections: int, horizon_rejections: int, usage_totals: dict, budget: MotionBudget
    ) -> None:
        pass


def notify(observer: TaskObserver | None, record: Callable[[TaskObserver], None]) -> None:
    """Hand one record to ``observer``, if any; its errors are swallowed, so it never breaks a task."""
    if observer is None:
        return
    try:
        record(observer)
    except Exception:  # noqa: BLE001 - an observer never breaks a task
        pass


class TeeLog:
    """A ``TaskLog`` that writes each record to ``log`` and then hands it to ``observer``. An observer error is
    swallowed, so presentation can never break a task or the log (dispatcher/docs/run-log.md)."""

    def __init__(self, log: TaskLog, observer: TaskObserver):
        self._log = log
        self._observer = observer

    @property
    def path(self) -> Path | None:
        return self._log.path

    def close(self) -> None:
        self._log.close()

    def _notify(self, record: Callable[[TaskObserver], None]) -> None:
        notify(self._observer, record)

    def task_start(
        self,
        task: str,
        source: str,
        sender_id: str | None,
        previous: TaskSummary | None,
        posture: Posture,
        start_state: StartState,
    ) -> None:
        self._log.task_start(task, source, sender_id, previous, posture, start_state)
        self._notify(lambda o: o.task_start(task, source, sender_id, previous, posture, start_state))

    def llm_request(
        self, call_index: int, return_reason: RequestReason, retry_of: ReturnReason | None, user_text: str
    ) -> None:
        self._log.llm_request(call_index, return_reason, retry_of, user_text)
        self._notify(lambda o: o.llm_request(call_index, return_reason, retry_of, user_text))

    def llm_retry(self, retry: InfraRetry) -> None:
        self._log.llm_retry(retry)
        self._notify(lambda o: o.llm_retry(retry))

    def llm_response(self, call_index: int, res: LLMResult) -> None:
        self._log.llm_response(call_index, res)
        self._notify(lambda o: o.llm_response(call_index, res))

    def llm_error(self, call_index: int, detail: str) -> None:
        self._log.llm_error(call_index, detail)
        self._notify(lambda o: o.llm_error(call_index, detail))

    def llm_interrupted(self, call_index: int, cause: str) -> None:
        self._log.llm_interrupted(call_index, cause)
        self._notify(lambda o: o.llm_interrupted(call_index, cause))

    def plan(self, call_index: int, plan: Plan, stop_at: int) -> None:
        self._log.plan(call_index, plan, stop_at)
        self._notify(lambda o: o.plan(call_index, plan, stop_at))

    def plan_invalid(self, call_index: int, tool_input: Any, check: PlanCheck, horizon: int) -> None:
        self._log.plan_invalid(call_index, tool_input, check, horizon)
        self._notify(lambda o: o.plan_invalid(call_index, tool_input, check, horizon))

    def step_start(self, ref: StepRef, dispatch: StepDispatch) -> None:
        self._log.step_start(ref, dispatch)
        self._notify(lambda o: o.step_start(ref, dispatch))

    def step_result(self, sr: StepResult, *, budget: MotionBudget, failures: int, posture: Posture) -> None:
        self._log.step_result(sr, budget=budget, failures=failures, posture=posture)
        self._notify(lambda o: o.step_result(sr, budget=budget, failures=failures, posture=posture))

    def precondition_failed(self, failure: PreconditionFailure) -> None:
        self._log.precondition_failed(failure)
        self._notify(lambda o: o.precondition_failed(failure))

    def stop_requested(self, source: str, during: str) -> None:
        self._log.stop_requested(source, during)
        self._notify(lambda o: o.stop_requested(source, during))

    def stop_move(self, smr: StopMoveResult) -> None:
        self._log.stop_move(smr)
        self._notify(lambda o: o.stop_move(smr))

    def exception(self, where: str, e: BaseException) -> None:
        self._log.exception(where, e)
        self._notify(lambda o: o.exception(where, e))

    def task_end(
        self, result: TaskOutcome, *, rejections: int, horizon_rejections: int, usage_totals: dict, budget: MotionBudget
    ) -> None:
        self._log.task_end(
            result,
            rejections=rejections,
            horizon_rejections=horizon_rejections,
            usage_totals=usage_totals,
            budget=budget,
        )
        self._notify(
            lambda o: o.task_end(
                result,
                rejections=rejections,
                horizon_rejections=horizon_rejections,
                usage_totals=usage_totals,
                budget=budget,
            )
        )
