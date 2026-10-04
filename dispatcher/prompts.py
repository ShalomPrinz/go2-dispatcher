"""Every fixed text the dispatcher and transports show to the LLM or the operator (dispatcher/docs/loop-and-context.md).

Wording is draft (docs/roadmap.md) and must stay identical across experimental conditions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .budget import (
    MOTION_BUDGET_MESSAGE,  # re-exported: defined once, in budget.py (dispatcher/docs/loop-and-context.md)
)
from .models import StepResult, TaskOutcomeCode

__all__ = [
    "SYSTEM_TEMPLATE",
    "SKILLS_HEADER",
    "system_text",
    "system_blocks",
    "SECTION_PREVIOUS_TASK",
    "SECTION_ROBOT",
    "SECTION_TASK",
    "SECTION_BUDGET",
    "SECTION_EXECUTED",
    "SECTION_REMAINING",
    "SECTION_NOTICE",
    "SECTION_SEPARATOR",
    "NONE",
    "NOTHING_YET",
    "OMITTED_ENTRIES",
    "PREVIOUS_TASK_TEMPLATE",
    "POSTURE_TEMPLATE",
    "BUDGET_TEMPLATE",
    "NOTICES",
    "notice",
    "REJECTION_HEADER",
    "REJECTION_FOOTER",
    "rejection_section",
    "MOTION_BUDGET_MESSAGE",
    "OPERATOR_MESSAGES",
    "STOP_MOVE_WARNING",
    "OutcomeFacts",
    "operator_message",
    "BUSY",
    "STOPPING",
    "NOTHING_RUNNING",
    "WORKING",
    "EMPTY_TASK",
    "HELP",
    "help_text",
]

# --- System (S1, S2), dispatcher/docs/loop-and-context.md ---------------------------------------------------

SYSTEM_TEMPLATE = """\
You plan actions for a Unitree Go2 quadruped robot. An operator gives you a task in natural language. You answer by calling the submit_plan tool exactly once.

How the loop works:
- You return a plan: an ordered list of skill calls. The system runs the steps in order and then calls you again with the results.
- Each call is independent. Everything you know is in the message: the previous task, the robot's posture, the task, the remaining budget, the steps already executed, and any steps left over from your previous plan.
- A new plan replaces any previous plan. Steps listed under "Remaining plan" do not run unless you include them again.

Rules:
- Use only skills from the skill list, with only their declared parameters, and keep values inside the declared ranges.
- A plan may contain at most {horizon} steps.
- status PLAN: one or more steps to run. message is optional.
- status DONE: the task is complete. No steps. message is required: tell the operator what was done and anything they asked to find out.
- status ABORT: the task cannot or should not be done with these skills, or it is ambiguous. No steps. message is required: explain why, or ask the operator a clarifying question.
- replan_after N: stop after step N and call you again before running the rest. Use it when later steps depend on what earlier steps find. Omit it to run the whole plan.
- If a step fails, the rest of that plan is abandoned and you are called again with the failure.
- The motion budget limits total travel and total rotation for this task. A plan that would exceed it is rejected before any step runs."""

SKILLS_HEADER = "## Skills\n"


def system_text(horizon: int) -> str:
    """S1: ``system[0]``."""
    return SYSTEM_TEMPLATE.format(horizon=horizon)


def system_blocks(horizon: int, catalog_text: str) -> list[str]:
    """``[system[0], system[1]]`` = [S1, ``"## Skills\\n" + catalog``] (dispatcher/docs/loop-and-context.md)."""
    return [system_text(horizon), SKILLS_HEADER + catalog_text]


# --- User message (dispatcher/docs/loop-and-context.md) -----------------------------------------------------------

SECTION_PREVIOUS_TASK = "## Previous task"
SECTION_ROBOT = "## Robot"
SECTION_TASK = "## Task"
SECTION_BUDGET = "## Budget"
SECTION_EXECUTED = "## Executed so far"
SECTION_REMAINING = "## Remaining plan"
SECTION_NOTICE = "## Notice"
SECTION_SEPARATOR = "\n\n"

NONE = "(none)"
NOTHING_YET = "(nothing yet)"
OMITTED_ENTRIES = "({n} earlier entries omitted)"

PREVIOUS_TASK_TEMPLATE = "Task: {task}\nOutcome: {outcome}\nMessage: {message}\nLast step: {last_step}"
POSTURE_TEMPLATE = "Posture: {posture}"
BUDGET_TEMPLATE = (
    "Travel: {travel_used} of {travel_max} m used\n"
    "Rotation: {rotation_used} of {rotation_max} deg used\n"
    "Failures: {failures} of {max_failures}\n"
    "Model calls: {calls_made} of {max_llm_calls}"
)

# --- Notices (dispatcher/docs/loop-and-context.md) ------------------------------------------------------------------------

NOTICES: dict[str, str] = {
    "initial": NONE,
    "plan_complete": (
        "Your previous plan ran to completion. Return DONE with a message if the "
        "task is complete; otherwise plan the next steps."
    ),
    "checkpoint": (
        "You asked to review results after step {n} of your previous plan. Its "
        'remaining steps are listed under "Remaining plan"; include them again if '
        "you still want them."
    ),
    "failure": (
        "Your previous plan failed at step {n} ({skill}): {outcome}. This is failure "
        "{f} of {max_failures}. Revise the plan to avoid that failure, or return ABORT "
        "with a message if the task cannot be done."
    ),
}


def notice(reason: str, **args: Any) -> str:
    """Notice for a return reason. ``checkpoint`` needs ``n``; ``failure`` needs ``n``,
    ``skill``, ``outcome``, ``f``, ``max_failures``. Unknown reason → KeyError."""
    return NOTICES[reason].format(**args)


# --- Rejection section (schema retry only) ----------------------------------------------------

REJECTION_HEADER = "## Your previous reply was rejected"
REJECTION_FOOTER = "Call submit_plan again with a corrected plan."


def rejection_section(errors: list[str]) -> str:
    """One line per validation error (each collapsed to one line)."""
    lines = [" ".join(str(e).split()) for e in errors]
    return "\n".join([REJECTION_HEADER, *lines, REJECTION_FOOTER])


# --- Operator messages (TaskOutcome.message) ---------------------------------------------------

OPERATOR_MESSAGES: dict[str, str] = {
    "DONE": "{message}",
    "ABORTED": "{message}",
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
    "INTERNAL_ERROR": "Stopped: internal error ({exception_type}). See run log {run_id}.",
}
STOP_MOVE_WARNING = " WARNING: the stop command to the robot failed. Stop the robot manually."


@dataclass(frozen=True)
class OutcomeFacts:
    """What an operator message is filled from; each outcome's text uses only the fields it names."""

    run_id: str
    time_limit_s: float
    max_llm_calls: int
    failures: int
    last_failure: StepResult | None = None
    message: str | None = None  # DONE / ABORTED: the model's message
    detail: str = ""  # LLM_ERROR
    exception_type: str = "Exception"  # INTERNAL_ERROR


def _operator_args(outcome: TaskOutcomeCode, facts: OutcomeFacts) -> dict[str, Any]:
    if outcome in ("DONE", "ABORTED"):
        return {"message": facts.message or ""}
    if outcome == "TIME_LIMIT_EXCEEDED":
        return {"limit": facts.time_limit_s}
    if outcome == "FAILURE_BUDGET_EXHAUSTED":
        lf = facts.last_failure
        return {
            "n": facts.failures,
            "skill": lf.ref.skill if lf else "",
            "error_message": (lf.error_message or lf.outcome) if lf else "",
        }
    if outcome == "CALL_BUDGET_EXHAUSTED":
        return {"n": facts.max_llm_calls}
    if outcome == "LLM_ERROR":
        return {"detail": facts.detail}
    if outcome == "INTERNAL_ERROR":
        return {"exception_type": facts.exception_type, "run_id": facts.run_id}
    return {}


def operator_message(outcome: TaskOutcomeCode, facts: OutcomeFacts, *, stop_move_failed: bool = False) -> str:
    """Operator text for ``outcome``, plus the StopMove warning if a StopMove failed."""
    text = OPERATOR_MESSAGES[outcome].format(**_operator_args(outcome, facts))
    return text + STOP_MOVE_WARNING if stop_move_failed else text


# --- Transport texts (docs/running.md) -------------------------------------------------------------------

BUSY = 'Busy: a task is running. Send "stop" to stop it.'
STOPPING = "Stopping."
NOTHING_RUNNING = "Nothing is running."
WORKING = "Working on it."
EMPTY_TASK = "Send a task, for example: walk forward one metre."
HELP = 'I control the Go2 robot. Send a task in plain words. Send "stop" to stop the current task. Backend: {backend}.'


def help_text(backend: str) -> str:
    return HELP.format(backend=backend)
