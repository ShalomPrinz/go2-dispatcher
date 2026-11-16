"""The run-log events: one frozen dataclass per record type, plus the two state-stream events
(dispatcher/docs/run-log.md)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeAlias

if TYPE_CHECKING:
    from contract.constants import Link

    from ..llm.client import InfraRetry, LLMResult
    from ..models import (
        Posture,
        PreconditionFailure,
        RequestReason,
        ReturnReason,
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


@dataclass(frozen=True)
class TaskStart:
    task: str
    source: str
    sender_id: str | None
    previous: TaskSummary | None
    posture: Posture
    start_state: StartState


@dataclass(frozen=True)
class LlmRequest:
    call_index: int
    return_reason: RequestReason
    retry_of: ReturnReason | None
    user_text: str


@dataclass(frozen=True)
class LlmRetry:
    retry: InfraRetry


@dataclass(frozen=True)
class LlmResponse:
    call_index: int
    result: LLMResult


@dataclass(frozen=True)
class LlmError:
    call_index: int
    detail: str


@dataclass(frozen=True)
class LlmInterrupted:
    call_index: int
    cause: str


@dataclass(frozen=True)
class PlanRecord:
    """The ``plan`` record; not ``Plan``, which is the plan model."""

    call_index: int
    plan: Plan
    stop_at: int


@dataclass(frozen=True)
class PlanInvalid:
    call_index: int
    tool_input: Any
    check: PlanCheck
    horizon: int


@dataclass(frozen=True)
class StepStart:
    ref: StepRef
    dispatch: StepDispatch


@dataclass(frozen=True)
class StepRecord:
    """The ``step_result`` record."""

    result: StepResult
    budget: MotionBudget
    failures: int
    posture: Posture


@dataclass(frozen=True)
class PreconditionFailed:
    failure: PreconditionFailure


@dataclass(frozen=True)
class StopRequested:
    source: str
    during: str


@dataclass(frozen=True)
class StopMove:
    result: StopMoveResult


@dataclass(frozen=True)
class ExceptionRaised:
    """The ``exception`` record."""

    where: str
    error: BaseException


@dataclass(frozen=True)
class TaskEnd:
    result: TaskOutcome
    rejections: int
    horizon_rejections: int
    usage_totals: dict
    budget: MotionBudget


@dataclass(frozen=True)
class RobotSample:
    """A state-stream sample, emitted on the stream thread; not a task record."""

    state: RobotState
    link: Link
    rx_mono: float | None


@dataclass(frozen=True)
class RobotLink:
    """A state-stream link change (``rx_mono`` None for the subscription's link); not a task record."""

    link: Link
    rx_mono: float | None


TaskEvent: TypeAlias = (
    TaskStart
    | LlmRequest
    | LlmRetry
    | LlmResponse
    | LlmError
    | LlmInterrupted
    | PlanRecord
    | PlanInvalid
    | StepStart
    | StepRecord
    | PreconditionFailed
    | StopRequested
    | StopMove
    | ExceptionRaised
    | TaskEnd
)
Event: TypeAlias = TaskEvent | RobotSample | RobotLink
