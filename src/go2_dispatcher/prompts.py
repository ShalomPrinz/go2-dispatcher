"""Every fixed text the dispatcher and transports show to the LLM or the operator (§11).

Wording is draft (OD-9) and must stay identical across experimental conditions.
"""

from __future__ import annotations

from typing import Any

from .budget import MOTION_BUDGET_MESSAGE  # re-exported: defined once, in budget.py (§11.8)
from .models import TaskOutcomeCode

__all__ = [
    "SYSTEM_TEMPLATE", "SKILLS_HEADER", "system_text", "system_blocks",
    "SECTION_PREVIOUS_TASK", "SECTION_ROBOT", "SECTION_TASK", "SECTION_BUDGET",
    "SECTION_EXECUTED", "SECTION_REMAINING", "SECTION_NOTICE", "SECTION_SEPARATOR",
    "NONE", "NOTHING_YET", "OMITTED_ENTRIES",
    "PREVIOUS_TASK_TEMPLATE", "POSTURE_TEMPLATE", "BUDGET_TEMPLATE",
    "NOTICES", "notice",
    "REJECTION_HEADER", "REJECTION_FOOTER", "rejection_section",
    "MOTION_BUDGET_MESSAGE",
    "OPERATOR_MESSAGES", "STOP_MOVE_WARNING", "operator_message",
    "BUSY", "STOPPING", "NOTHING_RUNNING", "WORKING", "EMPTY_TASK", "HELP", "help_text",
]

# --- System (S1, S2) — §11.1, §11.2 ---------------------------------------------------

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
    """``[system[0], system[1]]`` = [S1, ``"## Skills\\n" + catalog``] (§11.1)."""
    return [system_text(horizon), SKILLS_HEADER + catalog_text]


# --- User message (§11.3–§11.7) -----------------------------------------------------------

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

PREVIOUS_TASK_TEMPLATE = (
    "Task: {task}\n"
    "Outcome: {outcome}\n"
    "Message: {message}\n"
    "Last step: {last_step}"
)
POSTURE_TEMPLATE = "Posture: {posture}"
BUDGET_TEMPLATE = (
    "Travel: {travel_used} of {travel_max} m used\n"
    "Rotation: {rotation_used} of {rotation_max} deg used\n"
    "Failures: {failures} of {max_failures}\n"
    "Model calls: {calls_made} of {max_llm_calls}"
)

# --- Notices (§11.8) ------------------------------------------------------------------------

NOTICES: dict[str, str] = {
    "initial": NONE,
    "plan_complete": ("Your previous plan ran to completion. Return DONE with a message if the "
                      "task is complete; otherwise plan the next steps."),
    "checkpoint": ("You asked to review results after step {n} of your previous plan. Its "
                   "remaining steps are listed under \"Remaining plan\"; include them again if "
                   "you still want them."),
    "failure": ("Your previous plan failed at step {n} ({skill}): {outcome}. This is failure "
                "{f} of {max_failures}. Revise the plan to avoid that failure, or return ABORT "
                "with a message if the task cannot be done."),
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
    "TIME_LIMIT_EXCEEDED": ("Stopped: the task exceeded its {limit:g}s time limit. "
                            "A stop command was sent to the robot."),
    "FAILURE_BUDGET_EXHAUSTED": ("Stopped: the robot failed {n} times while trying this task. "
                                 "Last failure: {skill}: {error_message}"),
    "CALL_BUDGET_EXHAUSTED": ("Stopped: the task reached the limit of {n} planning calls "
                              "without finishing."),
    "LLM_INVALID": "Stopped: the model returned an invalid plan twice.",
    "LLM_ERROR": "Stopped: the model could not be reached ({detail}).",
    "INTERNAL_ERROR": "Stopped: internal error ({exception_type}). See run log {run_id}.",
}
STOP_MOVE_WARNING = " WARNING: the stop command to the robot failed. Stop the robot manually."


def operator_message(outcome: TaskOutcomeCode, *, stop_move_failed: bool = False,
                     **args: Any) -> str:
    """Operator text for ``outcome``. Arguments by outcome: DONE/ABORTED ``message``;
    TIME_LIMIT_EXCEEDED ``limit``; FAILURE_BUDGET_EXHAUSTED ``n``, ``skill``,
    ``error_message``; CALL_BUDGET_EXHAUSTED ``n``; LLM_ERROR ``detail``;
    INTERNAL_ERROR ``exception_type``, ``run_id``."""
    text = OPERATOR_MESSAGES[outcome].format(**args)
    return text + STOP_MOVE_WARNING if stop_move_failed else text


# --- Transport texts (§16) -------------------------------------------------------------------

BUSY = 'Busy: a task is running. Send "stop" to stop it.'
STOPPING = "Stopping."
NOTHING_RUNNING = "Nothing is running."
WORKING = "Working on it."
EMPTY_TASK = "Send a task, for example: walk forward one metre."
HELP = ('I control the Go2 robot. Send a task in plain words. Send "stop" to stop the '
        'current task. Backend: {backend}.')


def help_text(backend: str) -> str:
    return HELP.format(backend=backend)
