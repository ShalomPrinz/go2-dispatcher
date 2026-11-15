"""The operator's text for each task outcome (dispatcher/docs/loop-and-context.md).

The model reads these too, in the next task's Previous task section, so they are fixed texts like prompts.py.
Wording is draft (docs/roadmap.md) and must stay identical across experimental conditions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .context import fault_note
from .models import PreconditionFailure, StepResult, TaskOutcomeCode

__all__ = [
    "OPERATOR_MESSAGES",
    "SERVICE_LOST_STOP_CONFIRMED",
    "SERVICE_LOST_STOP_FAILED",
    "DONE_DEFAULT_MESSAGE",
    "STOP_MOVE_WARNING",
    "OutcomeFacts",
    "operator_message",
]

OPERATOR_MESSAGES: dict[str, str] = {
    "DONE": "{message}",
    "ABORTED": "{message}",
    "ASKED": "Question: {message}",
    "STOPPED": "Stopped on request. A stop command was sent to the robot.",
    "TIME_LIMIT_EXCEEDED": (
        "Stopped: the task exceeded its {limit:g}s time limit. A stop command was sent to the robot."
    ),
    "FAILURE_BUDGET_EXHAUSTED": (
        "Stopped: the robot failed {n} times while trying this task. Last failure: {skill}: {error_message}"
    ),
    "CALL_BUDGET_EXHAUSTED": ("Stopped: the task reached the limit of {n} planning calls without finishing."),
    "LLM_INVALID": "Stopped: the model returned an invalid plan twice.",
    "LLM_ERROR": "Stopped: the model could not be reached ({detail}).",
    "STATE_UNAVAILABLE": "Stopped: the robot state could not be read ({detail}).",
    "PRECONDITION_FAILED": "Stopped: {skill} needs the robot to be {required}, but its posture is {posture}.",
    "ROBOT_SERVICE_LOST": "Stopped: the robot control service was lost ({detail}).",
    "STOP_FAILED": "Stopped: the robot may still be moving.",  # the always-set warning names the action
    "INTERNAL_ERROR": "Stopped: internal error ({exception_type}). See run log {run_id}.",
}
# ROBOT_SERVICE_LOST details: whether the fallback stop was confirmed (dispatcher/docs/loop-and-context.md)
SERVICE_LOST_STOP_CONFIRMED = "the fallback stop was confirmed"
SERVICE_LOST_STOP_FAILED = "the fallback stop failed"
DONE_DEFAULT_MESSAGE = "Task completed, LLM didn't provide more details."  # DONE without a message
STOP_MOVE_WARNING = " WARNING: the stop command to the robot failed. Stop the robot manually."


@dataclass(frozen=True)
class OutcomeFacts:
    """What an operator message is filled from; each outcome's text uses only the fields it names."""

    run_id: str
    time_limit_s: float
    max_llm_calls: int
    failures: int
    last_failure: StepResult | None = None
    message: str | None = None  # DONE / ABORTED / ASKED: the model's message
    detail: str = ""  # LLM_ERROR, STATE_UNAVAILABLE, ROBOT_SERVICE_LOST
    exception_type: str = "Exception"  # INTERNAL_ERROR
    precondition: PreconditionFailure | None = None  # PRECONDITION_FAILED


def _last_failure(lf: StepResult | None) -> str:
    """An ``ok`` step: its failed verdict. Else the step's message (or its outcome), then any detected fault."""
    if lf is None:
        return ""
    note = fault_note(lf)
    if lf.outcome == "ok":
        return note or lf.outcome
    return "; ".join(filter(None, [lf.error_message or lf.outcome, note]))


def _operator_args(outcome: TaskOutcomeCode, facts: OutcomeFacts) -> dict[str, Any]:
    if outcome == "DONE":
        return {"message": facts.message if facts.message and facts.message.strip() else DONE_DEFAULT_MESSAGE}
    if outcome in ("ABORTED", "ASKED"):
        return {"message": facts.message or ""}
    if outcome == "TIME_LIMIT_EXCEEDED":
        return {"limit": facts.time_limit_s}
    if outcome == "FAILURE_BUDGET_EXHAUSTED":
        lf = facts.last_failure
        return {"n": facts.failures, "skill": lf.ref.skill if lf else "", "error_message": _last_failure(lf)}
    if outcome == "CALL_BUDGET_EXHAUSTED":
        return {"n": facts.max_llm_calls}
    if outcome in ("LLM_ERROR", "STATE_UNAVAILABLE", "ROBOT_SERVICE_LOST"):
        return {"detail": facts.detail}
    if outcome == "PRECONDITION_FAILED":
        pf = facts.precondition
        assert pf is not None  # the loop sets it with this outcome
        return {"skill": pf.ref.skill, "required": " or ".join(pf.requires), "posture": pf.posture}
    if outcome == "INTERNAL_ERROR":
        return {"exception_type": facts.exception_type, "run_id": facts.run_id}
    return {}


def operator_message(outcome: TaskOutcomeCode, facts: OutcomeFacts, *, stop_move_failed: bool = False) -> str:
    """Operator text for ``outcome``, plus the StopMove warning if a StopMove failed."""
    text = OPERATOR_MESSAGES[outcome].format(**_operator_args(outcome, facts))
    return text + STOP_MOVE_WARNING if stop_move_failed else text
