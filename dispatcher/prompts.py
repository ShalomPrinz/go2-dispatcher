"""Every fixed wording the LLM reads, and every operator-facing task text (dispatcher/docs/loop-and-context.md).

Console diagnostics and start-up errors stay in their modules, as do the tool schema and the step-line shapes.
Wording is draft (docs/roadmap.md) and must stay identical across experimental conditions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .budget import (
    MOTION_BUDGET_MESSAGE,  # re-exported: defined once, in budget.py (dispatcher/docs/loop-and-context.md)
)

if TYPE_CHECKING:
    from .models import StepResult, TaskOutcomeCode  # models imports this module for the kill messages

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
    "OBSTACLES_TEMPLATE",
    "OBSTACLES_UNAVAILABLE",
    "NO_READING",
    "BUDGET_TEMPLATE",
    "NOTICES",
    "notice",
    "REJECTION_HEADER",
    "REJECTION_FOOTER",
    "rejection_section",
    "ERR_MAX_TOKENS",
    "ERR_NO_TOOL_CALL",
    "HORIZON_EXCEEDED",
    "PLAN_NEEDS_STEPS",
    "STATUS_NO_STEPS",
    "STATUS_NEEDS_MESSAGE",
    "REPLAN_ONLY_PLAN",
    "REPLAN_OUT_OF_RANGE",
    "UNKNOWN_SKILL",
    "UNKNOWN_PARAM",
    "MISSING_PARAM",
    "PARAM_WRONG_TYPE",
    "EXPECTED_NUMBER",
    "EXPECTED_STRING",
    "EXPECTED_ENUM",
    "PARAM_VALUE",
    "PARAM_OUTSIDE_RANGE",
    "PARAM_BELOW_MIN",
    "PARAM_ABOVE_MAX",
    "MOTION_BUDGET_MESSAGE",
    "MALFORMED_RESPONSE",
    "KILL_STEP_TIMEOUT",
    "KILL_OPERATOR",
    "KILL_TASK_TIME_LIMIT",
    "KILL_SHUTDOWN",
    "KILL_ROBOT_SERVICE_LOST",
    "NOT_DISPATCHED_PREFIX",
    "OPERATOR_MESSAGES",
    "SERVICE_LOST_STOP_CONFIRMED",
    "SERVICE_LOST_STOP_FAILED",
    "DONE_DEFAULT_MESSAGE",
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
    "STEPS_LINE",
    "OMITTED_LINES",
    "ERROR_REPLY",
    "TELEGRAM_OUTCOMES",
    "TELEGRAM_STOP_MOVE_WARNING",
    "TELEGRAM_LAST_FAILURE",
    "STEP_REASONS",
    "STEP_REASON_FALLBACK",
    "PROGRESS_THINKING",
    "PROGRESS_PLAN",
    "PROGRESS_PLAN_MESSAGE",
    "PROGRESS_PLAN_STEP",
    "PROGRESS_PLAN_DONE",
    "PROGRESS_PLAN_ABORT",
    "PROGRESS_PLAN_MALFORMED",
    "PROGRESS_PLAN_OVER_BUDGET",
    "PROGRESS_ASKING_AGAIN",
    "PROGRESS_GIVING_UP",
    "PROGRESS_STEP_RUNNING",
    "PROGRESS_STEP_OK",
    "PROGRESS_STEP_FAILED",
    "PROGRESS_DURATION",
    "PROGRESS_STOP_REQUESTED",
    "PROGRESS_STOP_MOVE_SENT",
    "PROGRESS_STOP_MOVE_FAILED",
]

# --- System (S1, S2), dispatcher/docs/loop-and-context.md ---------------------------------------------------

# the obstacle margin (0.3 m) in the text is a starting value (tunable) (dispatcher/docs/loop-and-context.md)
SYSTEM_TEMPLATE = """\
You plan actions for a Unitree Go2 quadruped robot. An operator gives you a task in natural language. You answer by calling the submit_plan tool exactly once.

How the loop works:
- You return a plan: an ordered list of skill calls. The system runs the steps in order and then calls you again with the results.
- Each call is independent. Everything you know is in the message: the previous task, the robot's posture and obstacle distances, the task, the remaining budget, the steps already executed, and any steps left over from your previous plan.
- A new plan replaces any previous plan. Steps listed under "Remaining plan" do not run unless you include them again.

Rules:
- Use only skills from the skill list, with only their declared parameters, and keep values inside the declared ranges.
- A plan may contain at most {horizon} steps.
- status PLAN: one or more steps to run. message is optional.
- status DONE: the task is complete. No steps. message is required: tell the operator what was done and anything they asked to find out.
- status ABORT: the task cannot or should not be done with these skills, or it is ambiguous. No steps. message is required: explain why, or ask the operator a clarifying question.
- A message that is not a robot task (a greeting, small talk, a general question): status DONE. No steps. message: one sentence that replies and says what the robot can do.
- replan_after N: stop after step N and call you again before running the rest. Use it when later steps depend on what earlier steps find. Omit it to run the whole plan.
- If a step fails, the rest of that plan is abandoned and you are called again with the failure.
- The motion budget limits total travel and total rotation for this task. A plan that would exceed it is rejected before any step runs.

Obstacle distances:
- The Robot section gives the distance in metres to the nearest obstacle in front of, behind, left of and right of the robot, measured when the robot state was last read. "no reading" means the sensor reported nothing in that direction; "unavailable" means no distances are known.
- The values are a snapshot. Any walk or turn makes them stale. To use distances after a walk or turn, set replan_after to that step and plan the rest with the new values.
- A value is the nearest thing the sensor detected in that direction. It does not prove that the path of a walk is clear and does not say what the object is. The sensor misses glass, thin objects and low objects. "no reading" and "unavailable" do not mean the way is clear. The values are not a safety stop.
- Never plan a walk farther than the distance in its direction (forward: front, backward: back, left: left, right: right) minus 0.3 m. If the task needs more, walk the shorter distance and say why in message, or return ABORT with a message that explains or asks the operator.
- For tasks that ask about distances, such as "walk to the wall" or "how far is the nearest thing", use the values directly."""

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
OBSTACLES_TEMPLATE = "Obstacles (m): front {front}, back {back}, left {left}, right {right}"
OBSTACLES_UNAVAILABLE = "Obstacles (m): unavailable"
NO_READING = "no reading"
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


# --- Plan validation errors (dispatcher/docs/llm.md) -------------------------------------------

ERR_MAX_TOKENS = (
    "reply hit the output token limit before submit_plan was complete; reason more briefly and submit the plan sooner"
)
ERR_NO_TOOL_CALL = "no submit_plan call in reply"
HORIZON_EXCEEDED = "plan has {n} steps; the maximum is {horizon}"
PLAN_NEEDS_STEPS = "status PLAN needs at least one step"
STATUS_NO_STEPS = "status {status} must have no steps"
STATUS_NEEDS_MESSAGE = "status {status} needs a message"
REPLAN_ONLY_PLAN = "replan_after is only allowed with status PLAN"
REPLAN_OUT_OF_RANGE = "replan_after must be between 1 and {n}"

# --- Step errors and step lines (dispatcher/docs/loop-and-context.md) -----------------------------

UNKNOWN_SKILL = "unknown skill '{skill}'; available: {available}"
UNKNOWN_PARAM = "unknown parameter '{param}' for skill {skill}"
MISSING_PARAM = "missing parameter '{param}' for skill {skill}"
PARAM_WRONG_TYPE = "parameter '{param}' for skill {skill} must be {expected}, got {value}"
EXPECTED_NUMBER = "a finite number"
EXPECTED_STRING = "a non-empty string"
EXPECTED_ENUM = "one of {values}"
PARAM_VALUE = "parameter '{param}' for skill {skill} is {value:g}"
PARAM_OUTSIDE_RANGE = "{where}, outside {lo:g} to {hi:g}"
PARAM_BELOW_MIN = "{where}, below the minimum {lo:g}"
PARAM_ABOVE_MAX = "{where}, above the maximum {hi:g}"

MALFORMED_RESPONSE = "skill process exited with code {rc} without a valid response"
KILL_STEP_TIMEOUT = "killed after {timeout_s:g}s timeout"
KILL_OPERATOR = "stopped by operator"
KILL_TASK_TIME_LIMIT = "task time limit reached"
KILL_SHUTDOWN = "dispatcher shutting down"
KILL_ROBOT_SERVICE_LOST = "stopped: the robot control service was lost"

NOT_DISPATCHED_PREFIX = "- rejected before running: "


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
    "STATE_UNAVAILABLE": "Stopped: the robot state could not be read ({detail}).",
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
    message: str | None = None  # DONE / ABORTED: the model's message
    detail: str = ""  # LLM_ERROR, STATE_UNAVAILABLE, ROBOT_SERVICE_LOST
    exception_type: str = "Exception"  # INTERNAL_ERROR


def _operator_args(outcome: TaskOutcomeCode, facts: OutcomeFacts) -> dict[str, Any]:
    if outcome == "DONE":
        return {"message": facts.message if facts.message and facts.message.strip() else DONE_DEFAULT_MESSAGE}
    if outcome == "ABORTED":
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
    if outcome in ("LLM_ERROR", "STATE_UNAVAILABLE", "ROBOT_SERVICE_LOST"):
        return {"detail": facts.detail}
    if outcome == "INTERNAL_ERROR":
        return {"exception_type": facts.exception_type, "run_id": facts.run_id}
    return {}


def operator_message(outcome: TaskOutcomeCode, facts: OutcomeFacts, *, stop_move_failed: bool = False) -> str:
    """Operator text for ``outcome``, plus the StopMove warning if a StopMove failed."""
    text = OPERATOR_MESSAGES[outcome].format(**_operator_args(outcome, facts))
    return text + STOP_MOVE_WARNING if stop_move_failed else text


# --- Transport texts (docs/running.md) -------------------------------------------------------------------
# Telegram texts are HTML (parse_mode HTML); the emoji is the colour code (docs/running.md).

BUSY = '⏳ Busy: a task is running. Send "stop" to stop it.'
STOPPING = "🛑 Stopping."
NOTHING_RUNNING = "ℹ️ Nothing is running."
WORKING = "🔵 Working on it."
EMPTY_TASK = "Send a task, for example: walk forward one metre."
HELP = 'I control the Go2 robot. Send a task in plain words. Send "stop" to stop the current task. Backend: {backend}.'


def help_text(backend: str) -> str:
    return HELP.format(backend=backend)


STEPS_LINE = "Steps: {run} run, {failed} failed"
OMITTED_LINES = "({n} earlier lines omitted)"
ERROR_REPLY = "❌ Internal error in the bot. The details are in its console output."


# --- Telegram outcome text: categories only, the detail is in the run log (docs/running.md) ----------------

TELEGRAM_OUTCOMES: dict[str, str] = {
    "DONE": "✅ <b>Done</b>: {message}",
    "ABORTED": "⚠️ <b>Aborted</b>: {message}",
    "STOPPED": "🛑 <b>Stopped</b> on request. A stop command was sent to the robot.",
    "TIME_LIMIT_EXCEEDED": "❌ <b>Stopped</b>: the task reached its time limit. A stop command was sent to the robot.",
    "FAILURE_BUDGET_EXHAUSTED": "❌ <b>Stopped</b>: the robot failed {n} times while trying this task.",
    "CALL_BUDGET_EXHAUSTED": "❌ <b>Stopped</b>: the task reached the limit of {n} planning calls without finishing.",
    "LLM_INVALID": "❌ <b>Stopped</b>: the model produced a malformed plan twice.",
    "LLM_ERROR": "❌ <b>Stopped</b>: the model could not be reached.",
    "STATE_UNAVAILABLE": "❌ <b>Stopped</b>: the robot state could not be read.",
    "ROBOT_SERVICE_LOST": "❌ <b>Stopped</b>: the robot control service was lost.",
    "STOP_FAILED": "❌ <b>Stopped</b>: the robot may still be moving.",
    "INTERNAL_ERROR": "❌ <b>Stopped</b>: internal error. See run log {run_id}.",
}
TELEGRAM_LAST_FAILURE = "Last failure: {skill} {reason}."
TELEGRAM_STOP_MOVE_WARNING = "⚠️ <b>The stop command to the robot failed. Stop the robot manually.</b>"

# Short reason for a failed step: keyed by step outcome, or by error code for outcome "error"
STEP_REASONS: dict[str, str] = {
    "timeout": "timed out",
    "interrupted": "was stopped",
    "malformed": "gave no valid answer",
    "rejected": "was in a malformed plan",
    "motion_budget_exceeded": "would exceed the motion budget",
    "invalid_params": "got invalid parameters",
    "sdk_error": "failed: robot command error",
    "backend_not_configured": "failed: backend not configured",
    "exception": "failed: skill error",
    "unsupported_object": "failed: object not supported",
    "detector_error": "failed: vision error",
    "camera_unavailable": "failed: camera unavailable",
    "bad_frame": "failed: camera unavailable",
    "weights_missing": "failed: vision error",
    "vision_unavailable": "failed: vision unavailable",
    "state_unavailable": "failed: robot state unavailable",
    "robot_unavailable": "failed: robot unavailable",
    "motion_rejected": "failed: the robot refused the move",
    "robot_service_lost": "failed: robot control service lost",
}
STEP_REASON_FALLBACK = "failed: {code}"  # an error code without a reason above, underscores as spaces


# --- Telegram progress message (docs/running.md) ------------------------------------------------------------

PROGRESS_THINKING = "🔵 <i>Thinking...</i>"
PROGRESS_PLAN = "ℹ️ <b>Plan</b>"
PROGRESS_PLAN_MESSAGE = "ℹ️ <b>Plan</b>: {message}"  # PLAN with its optional message
PROGRESS_PLAN_STEP = "{i}. {call}"
PROGRESS_PLAN_DONE = "✅ <b>Done</b>: {message}"
PROGRESS_PLAN_ABORT = "⚠️ <b>Aborted</b>: {message}"
PROGRESS_PLAN_MALFORMED = "❌ The model produced a malformed plan; {then}"
PROGRESS_PLAN_OVER_BUDGET = "❌ The plan would exceed the motion budget; {then}"
PROGRESS_ASKING_AGAIN = "asking again"
PROGRESS_GIVING_UP = "giving up"
PROGRESS_STEP_RUNNING = "▶️ {call}"
PROGRESS_STEP_OK = "✅ {step}"
PROGRESS_STEP_FAILED = "❌ {call} {reason}"
PROGRESS_DURATION = " ({seconds:.1f}s)"
PROGRESS_STOP_REQUESTED = "🛑 Stop requested ({source})"
PROGRESS_STOP_MOVE_SENT = "🛑 StopMove sent ({seconds:.1f}s)"
PROGRESS_STOP_MOVE_FAILED = "❌ <b>StopMove failed</b> ({seconds:.1f}s)"
