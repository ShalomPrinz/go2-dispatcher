"""Data models and exceptions (docs/architecture.md)."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr, model_validator

from skills.policy import MotionCost

# the response schema is owned by skills and re-exported here (skills/docs/skills.md)
from skills.schema import Posture as Posture
from skills.schema import RobotState as RobotState
from skills.schema import SkillError as SkillError
from skills.schema import SkillResponse as SkillResponse

# --- Exceptions (docs/architecture.md) -------------------------------------------------------


class StartupError(Exception):
    """The dispatcher cannot start; the entry point prints it and exits 2 (docs/running.md)."""


class ConfigError(StartupError):
    """Invalid configuration (unknown key, invalid value, missing explicit file)."""


class RegistryError(StartupError):
    """Invalid skill set; the message names the offending file."""


class BusyError(Exception):
    """run_task called while a task is running."""


class LLMUnavailable(Exception):
    """Infra retries exhausted, or a non-retryable API error."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class LLMInterrupted(Exception):
    """Stop (``OPERATOR``) or task deadline (``TASK_TIME_LIMIT``) hit while waiting to retry an LLM call."""

    def __init__(self, cause: StopCause):
        super().__init__(cause)
        self.cause = StopCause(cause)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Plan (dispatcher/docs/loop-and-context.md) -------------------------------------------------------------


class PlanStep(_Model):
    skill: StrictStr
    params: dict[str, Any] = {}


class Plan(_Model):
    status: Literal["PLAN", "DONE", "ABORT"]
    steps: list[PlanStep] = []
    replan_after: StrictInt | None = None  # 1-based step index within this plan
    message: StrictStr | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalise_status(cls, data: Any) -> Any:
        # the API does not guarantee enum casing in non-strict mode
        if isinstance(data, dict) and isinstance(data.get("status"), str):
            data = {**data, "status": data["status"].strip().upper()}
        return data


# --- StepResult (dispatcher/docs/run-log.md) --------------------------------------------------------

StepOutcome = Literal["ok", "error", "timeout", "malformed", "rejected", "motion_budget_exceeded", "interrupted"]
FAILURE_OUTCOMES = frozenset({"error", "timeout", "malformed", "rejected", "motion_budget_exceeded"})


class StopCause(str, Enum):
    """Why a task or a running step is cut short; the one place its mappings live
    (dispatcher/docs/loop-and-context.md). Values are the logged strings."""

    OPERATOR = "operator"
    SHUTDOWN = "shutdown"
    TASK_TIME_LIMIT = "task_time_limit"
    STEP_TIMEOUT = "step_timeout"
    INTERNAL_ERROR = "internal_error"

    @property
    def task_outcome(self) -> TaskOutcomeCode:
        """How the task ends when this cause interrupts it (OPERATOR, SHUTDOWN, TASK_TIME_LIMIT only)."""
        return _TASK_OUTCOMES[self]

    @property
    def kill(self) -> StepKill:
        """What a skill process killed for this cause reports (all but INTERNAL_ERROR)."""
        return _KILLS[self]


class StepKill(NamedTuple):
    outcome: Literal["timeout", "interrupted"]
    error_code: str
    error_message: str  # template; {timeout_s} is the step timeout


_TASK_OUTCOMES: dict[StopCause, TaskOutcomeCode] = {
    StopCause.OPERATOR: "STOPPED",
    StopCause.SHUTDOWN: "STOPPED",
    StopCause.TASK_TIME_LIMIT: "TIME_LIMIT_EXCEEDED",
}
_KILLS: dict[StopCause, StepKill] = {
    StopCause.STEP_TIMEOUT: StepKill("timeout", "timeout", "killed after {timeout_s:g}s timeout"),
    StopCause.OPERATOR: StepKill("interrupted", "stopped_by_operator", "stopped by operator"),
    StopCause.TASK_TIME_LIMIT: StepKill("interrupted", "task_time_limit", "task time limit reached"),
    StopCause.SHUTDOWN: StepKill("interrupted", "shutdown", "dispatcher shutting down"),
}
KILLED_OUTCOMES = frozenset(k.outcome for k in _KILLS.values())  # posture unknown after these


class StopMoveResult(_Model):
    ok: bool
    reason: StopCause
    duration_ms: float
    exit_code: int | None = None
    response: SkillResponse | None = None  # includes state_after sampled after StopMove
    stderr_tail: str | None = None


class StepDispatch(_Model):
    """What the dispatcher decided for a dispatched step; built once per step (dispatcher/docs/run-log.md)."""

    index: StrictInt  # 1-based count of dispatched steps in the task
    timeout_s: float
    motion_cost: MotionCost  # stdlib dataclass; dumps as {distance_m, rotation_deg}
    fault: str | None = None  # stub fault kind injected, if any


class StepRef(_Model):
    """Which plan step this is; every recorded step has one, built once (dispatcher/docs/run-log.md)."""

    call_index: StrictInt  # LLM call that produced the plan (1-based)
    plan_step: StrictInt  # 1-based position within that plan
    skill: str
    params: dict[str, Any]  # filled + normalised; raw as received for a bounds rejection


class StepResult(_Model):
    ref: StepRef
    dispatch: StepDispatch | None = None  # None if not dispatched (rejected)
    outcome: StepOutcome
    error_code: str | None = None
    error_message: str | None = None  # one line, <= 200 chars, safe for LLM context
    response: SkillResponse | None = None
    duration_ms: float = 0.0  # wall clock around the subprocess; 0 if not dispatched
    exit_code: int | None = None
    pid: int | None = None  # LOG ONLY
    stderr_tail: str | None = None  # last 2000 chars; LOG ONLY, never in context
    verification: Literal["unverified"] = "unverified"


# --- Task outcome and summary (dispatcher/docs/loop-and-context.md) -------------------------------------

TaskOutcomeCode = Literal[
    "DONE",
    "ABORTED",
    "STOPPED",
    "TIME_LIMIT_EXCEEDED",
    "FAILURE_BUDGET_EXHAUSTED",
    "CALL_BUDGET_EXHAUSTED",
    "LLM_INVALID",
    "LLM_ERROR",
    "INTERNAL_ERROR",
]


class TaskOutcome(_Model):
    run_id: str
    task: str
    outcome: TaskOutcomeCode
    message: str  # operator-facing (dispatcher/docs/loop-and-context.md)
    steps: list[StepResult]  # every recorded step, in order
    llm_calls: int
    failures: int
    stop_move_failed: bool = False
    duration_ms: float
    final_posture: Posture
    log_path: str


class TaskSummary(_Model):
    task: str
    outcome: TaskOutcomeCode
    message: str
    last_step: StepResult | None  # last dispatched step, if any
