"""Data models and exceptions (docs/architecture.md)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr, model_validator


# --- Exceptions (docs/architecture.md) -------------------------------------------------------


class ConfigError(Exception):
    """Invalid configuration (unknown key, invalid value, missing explicit file)."""


class RegistryError(Exception):
    """Invalid skill set; the message names the offending file."""


class BusyError(Exception):
    """run_task called while a task is running."""


class LLMUnavailable(Exception):
    """Infra retries exhausted, or a non-retryable API error."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class LLMInterrupted(Exception):
    """Stop or task deadline hit while waiting to retry an LLM call."""

    def __init__(self, cause: Literal["operator", "task_time_limit"]):
        super().__init__(cause)
        self.cause = cause


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Plan (docs/loop-and-context.md) -------------------------------------------------------------


class PlanStep(_Model):
    skill: StrictStr
    params: dict[str, Any] = {}


class Plan(_Model):
    status: Literal["PLAN", "DONE", "ABORT"]
    steps: list[PlanStep] = []
    replan_after: StrictInt | None = None      # 1-based step index within this plan
    message: StrictStr | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalise_status(cls, data: Any) -> Any:
        # the API does not guarantee enum casing in non-strict mode
        if isinstance(data, dict) and isinstance(data.get("status"), str):
            data = {**data, "status": data["status"].strip().upper()}
        return data


# --- SkillResponse (docs/skills.md) -----------------------------------------------------


class SkillError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str                               # one line, written for the LLM


class RobotState(BaseModel):
    model_config = ConfigDict(extra="allow")   # tolerate extra fields from the robot

    t: float                                   # unix time when sampled (local clock)
    backend: Literal["real", "stub"]
    posture: Literal["standing", "sitting", "unknown"]
    mode: int | None = None
    gait_type: int | None = None
    body_height: float | None = None
    position: list[float] | None = None        # [x, y, z], robot's own estimate if published
    velocity: list[float] | None = None        # [vx, vy, vz]
    yaw_speed: float | None = None
    imu_rpy: list[float] | None = None         # [roll, pitch, yaw] radians
    foot_force: list[float] | None = None      # 4 values
    error_code: int | None = None


class SkillResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    skill: str
    status: Literal["ok", "error"]
    observations: dict[str, Any] = {}
    error: SkillError | None = None            # present iff status == "error"
    state_before: RobotState | None = None
    state_after: RobotState | None = None
    state_error: str | None = None             # messages joined with "; "
    timing: dict[str, float] = {}              # init_ms, exec_ms, state_ms, total_ms (+ stop_call_ms)

    @model_validator(mode="after")
    def _error_iff_status_error(self) -> "SkillResponse":
        if (self.status == "error") != (self.error is not None):
            raise ValueError("error must be present if and only if status == 'error'")
        return self


# --- StepResult (docs/run-log.md) --------------------------------------------------------

StepOutcome = Literal["ok", "error", "timeout", "malformed",
                      "rejected", "motion_budget_exceeded", "interrupted"]
FAILURE_OUTCOMES = frozenset({"error", "timeout", "malformed", "rejected", "motion_budget_exceeded"})


class MotionCostModel(_Model):                  # pydantic mirror of policy_base.MotionCost
    distance_m: float = 0.0
    rotation_deg: float = 0.0


class StopMoveResult(_Model):
    ok: bool
    reason: Literal["operator", "task_time_limit", "step_timeout", "shutdown", "internal_error"]
    duration_ms: float
    exit_code: int | None = None
    response: SkillResponse | None = None      # includes state_after sampled after StopMove
    stderr_tail: str | None = None


class StepResult(_Model):
    index: int | None                  # 1-based count of dispatched steps in the task; None if not dispatched
    call_index: int                    # LLM call that produced the plan (1-based)
    plan_step: int                     # 1-based position within that plan
    skill: str
    params: dict[str, Any]             # dispatched: filled + normalised params; rejected: raw params as received
    outcome: StepOutcome
    error_code: str | None = None
    error_message: str | None = None   # one line, <= 200 chars, safe for LLM context
    response: SkillResponse | None = None
    duration_ms: float = 0.0           # wall clock around the subprocess; 0 if not dispatched
    timeout_s: float | None = None
    motion_cost: MotionCostModel = MotionCostModel()
    fault: str | None = None           # stub fault kind injected, if any
    exit_code: int | None = None
    pid: int | None = None             # LOG ONLY
    stderr_tail: str | None = None     # last 2000 chars; LOG ONLY, never in context
    stop_move: StopMoveResult | None = None
    verification: Literal["unverified"] = "unverified"


# --- Task outcome and summary (docs/loop-and-context.md) -------------------------------------

TaskOutcomeCode = Literal[
    "DONE", "ABORTED", "STOPPED", "TIME_LIMIT_EXCEEDED",
    "FAILURE_BUDGET_EXHAUSTED", "CALL_BUDGET_EXHAUSTED",
    "LLM_INVALID", "LLM_ERROR", "INTERNAL_ERROR",
]


class TaskOutcome(_Model):
    run_id: str
    task: str
    outcome: TaskOutcomeCode
    message: str                        # operator-facing (docs/loop-and-context.md)
    steps: list[StepResult]             # every recorded step, in order
    llm_calls: int
    failures: int
    stop_move_failed: bool = False
    duration_ms: float
    final_posture: Literal["standing", "sitting", "unknown"]
    log_path: str


class TaskSummary(_Model):
    task: str
    outcome: TaskOutcomeCode
    message: str
    last_step: StepResult | None        # last dispatched step, if any
