"""Data models and exceptions (docs/architecture.md)."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, SerializerFunctionWrapHandler, StrictInt, model_serializer

from contract.policy import MotionCost

# the response schema is owned by contract and re-exported here (contract/docs/contract.md)
from contract.schema import ObstacleDistances as ObstacleDistances
from contract.schema import Posture as Posture
from contract.schema import RobotState as RobotState
from contract.schema import SkillError as SkillError
from contract.schema import SkillResponse as SkillResponse

from . import prompts


class Model(BaseModel):
    """Shared base for the dispatcher's pydantic models: unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")


# --- Exceptions (docs/architecture.md) -------------------------------------------------------


class StartupError(Exception):
    """The dispatcher cannot start; the entry point prints it and exits 2 (docs/running.md)."""


class ConfigError(StartupError):
    """Invalid configuration (unknown key, invalid value, missing explicit file)."""


class RegistryError(StartupError):
    """Invalid skill set; the message names the offending file."""


class RobotServiceError(StartupError):
    """The robot service did not start; the text says why and where its log is (docs/running.md)."""


class StateReadError(Exception):
    """A state read through the robot service failed; the text says why, without a traceback."""


class RobotServiceLostError(StateReadError):
    """A pre-step read found the robot service dead or unreachable; the task ends ROBOT_SERVICE_LOST."""


class BusyError(Exception):
    """run_task called while a task is running."""


# --- Return reasons (dispatcher/docs/loop-and-context.md) --------------------------------------------

ReturnReason = Literal["initial", "plan_complete", "checkpoint", "failure"]
RequestReason = ReturnReason | Literal["schema_retry"]  # logged on llm_request (dispatcher/docs/run-log.md)


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
    STATE_UNAVAILABLE = "state_unavailable"  # a task-start or pre-step state read failed; StopMove only
    ROBOT_SERVICE_LOST = "robot_service_lost"  # the robot service died or hung; fallback stop (docs/safety.md)
    SERVICE_STOP_FAILED = "service_stop_failed"  # a StopMove the service sent on its own failed; fallback stop

    @property
    def task_outcome(self) -> TaskOutcomeCode:
        """How the task ends when this cause interrupts it (OPERATOR, SHUTDOWN, TASK_TIME_LIMIT only)."""
        return _TASK_OUTCOMES[self]

    @property
    def kill(self) -> StepKill:
        """What a skill process killed for this cause reports (all but INTERNAL_ERROR, STATE_UNAVAILABLE,
        ROBOT_SERVICE_LOST, which the executor reports as an ``error`` step, and SERVICE_STOP_FAILED, which kills
        nothing)."""
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
    StopCause.STEP_TIMEOUT: StepKill("timeout", "timeout", prompts.KILL_STEP_TIMEOUT),
    StopCause.OPERATOR: StepKill("interrupted", "stopped_by_operator", prompts.KILL_OPERATOR),
    StopCause.TASK_TIME_LIMIT: StepKill("interrupted", "task_time_limit", prompts.KILL_TASK_TIME_LIMIT),
    StopCause.SHUTDOWN: StepKill("interrupted", "shutdown", prompts.KILL_SHUTDOWN),
}
KILLED_OUTCOMES = frozenset(k.outcome for k in _KILLS.values())  # posture unknown after these


StopPath = Literal["service", "fallback"]


class StopMoveResult(Model):
    """One run of the stop path: the service's ``stop``, or the fresh-process ``stop_move`` (docs/safety.md)."""

    ok: bool
    reason: StopCause
    stop_path: StopPath | None  # None only when the stop path itself raised (dispatcher/docs/run-log.md)
    duration_ms: float  # the whole stop path, including a failed service stop before the fallback
    kill_to_stop_ms: float | None = None  # from the skill kill to the confirmed or failed stop; None without a kill
    stop_call_ms: float | None = None  # service: StopMove's call time in the service (stop_sent)
    service_error: str | None = None  # fallback: why the service stop was not used
    service_lost: bool = False  # the service was lost (and killed) on this stop path (dispatcher/docs/run-log.md)
    state_after: RobotState | None = None  # read after the stop; None if the read failed
    exit_code: int | None = None  # fallback
    response: SkillResponse | None = None  # fallback: the stop_move utility's response
    stderr_tail: str | None = None  # fallback: the stop_move process's stderr


class StepDispatch(Model):
    """What the dispatcher decided for a dispatched step; built once per step (dispatcher/docs/run-log.md)."""

    index: StrictInt  # 1-based count of dispatched steps in the task
    timeout_s: float
    motion_cost: MotionCost  # stdlib dataclass; dumps as {distance_m, rotation_deg}
    fault: str | None = None  # stub fault kind injected, if any


class StepRef(Model):
    """Which plan step this is; every recorded step has one, built once (dispatcher/docs/run-log.md)."""

    call_index: StrictInt  # LLM call that produced the plan (1-based)
    plan_step: StrictInt  # 1-based position within that plan
    skill: str
    params: dict[str, Any]  # filled + normalised; raw as received for a bounds rejection


Verdict = Literal["verified", "failed", "unverified", "unavailable"]
Fault = Literal["fallen", "fault_mode", "command_rejected"]


class ProcessRun(Model):
    """What one skill process left behind: its reply, exit code, pid and stderr tail (contract/docs/contract.md)."""

    response: SkillResponse | None = None  # None if stdout held no valid response line
    exit_code: int
    pid: int  # LOG ONLY
    stderr_tail: str | None = None  # last 2000 chars; LOG ONLY, never in context


ExecOutcome = Literal["ok", "error", "timeout", "malformed", "interrupted"]


class ExecResult(Model):
    """One step as the executor ran it (dispatcher/docs/execution.md)."""

    outcome: ExecOutcome
    interrupt_cause: StopCause | None = None  # set iff outcome is interrupted
    error_code: str | None = None
    error_message: str | None = None
    duration_ms: float
    process: ProcessRun | None = None  # None if the step never started
    stop_move: StopMoveResult | None = None

    @property
    def service_lost(self) -> bool:
        """The robot service was lost during the step and killed; owned by ``stop_move`` (docs/safety.md)."""
        return self.stop_move is not None and self.stop_move.service_lost

    @property
    def response(self) -> SkillResponse | None:
        return self.process.response if self.process is not None else None


# the step_result record's key order; ``process`` is flattened into it (dispatcher/docs/run-log.md)
_STEP_RESULT_KEYS = (
    "ref",
    "dispatch",
    "outcome",
    "error_code",
    "error_message",
    "response",
    "duration_ms",
    "exit_code",
    "pid",
    "stderr_tail",
    "verification",
    "faults",
)


class StepResult(Model):
    ref: StepRef
    dispatch: StepDispatch | None = None  # None if not dispatched (rejected)
    outcome: StepOutcome
    error_code: str | None = None
    error_message: str | None = None  # one line, <= 200 chars, safe for LLM context
    duration_ms: float = 0.0  # wall clock around the subprocess; 0 if not dispatched
    process: ProcessRun | None = None  # None if no skill process ran (rejected, or never started)
    verification: Verdict | None = None  # None: not dispatched, or the outcome is not ok (loop-and-context.md)
    faults: list[Fault] = []  # faults detected in the step's response; on an ok step any forces "failed"

    @model_serializer(mode="wrap")
    def _flatten_process(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """Dump ``process`` as top-level ``response``, ``exit_code``, ``pid``, ``stderr_tail`` (null without one)."""
        data = handler(self)
        process = data.pop("process", None) or {}
        data.update({k: process.get(k) for k in ProcessRun.model_fields})
        ordered = {k: data.pop(k) for k in _STEP_RESULT_KEYS if k in data}
        return ordered | data

    @property
    def response(self) -> SkillResponse | None:
        return self.process.response if self.process is not None else None

    @property
    def is_failure(self) -> bool:
        """A failing outcome, or a ``failed`` verdict, which only an ``ok`` step can have
        (dispatcher/docs/loop-and-context.md)."""
        return self.outcome in FAILURE_OUTCOMES or self.verification == "failed"


# --- Task outcome and summary (dispatcher/docs/loop-and-context.md) -------------------------------------

TaskOutcomeCode = Literal[
    "DONE",
    "ABORTED",
    "ASKED",
    "STOPPED",
    "TIME_LIMIT_EXCEEDED",
    "FAILURE_BUDGET_EXHAUSTED",
    "CALL_BUDGET_EXHAUSTED",
    "LLM_INVALID",
    "LLM_ERROR",
    "STATE_UNAVAILABLE",
    "PRECONDITION_FAILED",
    "ROBOT_SERVICE_LOST",
    "STOP_FAILED",
    "INTERNAL_ERROR",
]


class TaskOutcome(Model):
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

    @property
    def dispatched_steps(self) -> list[StepResult]:
        """Steps that reached the executor, in order."""
        return [s for s in self.steps if s.dispatch is not None]


class TaskSummary(Model):
    task: str
    outcome: TaskOutcomeCode
    message: str
    last_step: StepResult | None  # last dispatched step, if any


class StartState(Model):
    """The task-start state read through the robot service (dispatcher/docs/loop-and-context.md)."""

    age_ms: float | None  # age of the state message at the read; None when the read failed
    error: str | None = None  # why the read failed; the task then ends STATE_UNAVAILABLE


class PreconditionFailure(Model):
    """A step not dispatched because the robot's state did not meet its ``requires``
    (dispatcher/docs/loop-and-context.md)."""

    ref: StepRef
    requires: list[Posture]  # the skill's allowed postures
    state: RobotState  # the pre-step read the check used

    @property
    def posture(self) -> Posture:
        return self.state.posture
