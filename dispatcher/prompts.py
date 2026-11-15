"""Every fixed wording the LLM reads (dispatcher/docs/loop-and-context.md).

Operator outcome texts are in operator_messages.py, transport texts in transports/texts.py; console diagnostics,
start-up errors, the tool schema and the step-line shapes stay in their modules.
Wording is draft (docs/roadmap.md) and must stay identical across experimental conditions.
"""

from __future__ import annotations

from typing import Any

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
    "VERIFICATION_FAILED",
    "VERIFICATION_FAILED_DETAIL",
    "GOAL_NOT_REACHED",
    "FAULT_TEXTS",
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
- status ABORT: the task cannot or should not be done with these skills. No steps. message is required: explain why.
- status ASK: the task is ambiguous, and the robot state and the results so far cannot settle it. No steps. message is required: one clarifying question for the operator. The task ends; the answer arrives as a new task, with this task and your question under "Previous task".
- A message that is not a robot task (a greeting, small talk, a general question): status DONE. No steps. message: one sentence that replies and says what the robot can do.
- replan_after N: stop after step N and call you again before running the rest. Use it when later steps depend on what earlier steps find. Omit it to run the whole plan.
- If a step fails, the rest of that plan is abandoned and you are called again with the failure.
- The motion budget limits total travel and total rotation for this task. A plan that would exceed it is rejected before any step runs.

Obstacle distances:
- The Robot section gives the distance in metres to the nearest obstacle in front of, behind, left of and right of the robot, measured when the robot state was last read. "no reading" means the sensor reported nothing in that direction; "unavailable" means no distances are known.
- The values are a snapshot. Any walk or turn makes them stale. To use distances after a walk or turn, set replan_after to that step and plan the rest with the new values.
- A value is the nearest thing the sensor detected in that direction. It does not prove that the path of a walk is clear and does not say what the object is. The sensor misses glass, thin objects and low objects. "no reading" and "unavailable" do not mean the way is clear. The values are not a safety stop.
- Never plan a walk farther than the distance in its direction (forward: front, backward: back, left: left, right: right) minus 0.3 m. If the task needs more, walk the shorter distance and say why in message, or return ABORT with a message that explains, or ASK with a question for the operator.
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

# Motion-budget message; planning/budget.py fills it, kind -> unit there
MOTION_BUDGET_MESSAGE = "this step needs {need:g} {unit} of {kind} but only {left:g} {unit} remain for this task"

MALFORMED_RESPONSE = "skill process exited with code {rc} without a valid response"
KILL_STEP_TIMEOUT = "killed after {timeout_s:g}s timeout"
KILL_OPERATOR = "stopped by operator"
KILL_TASK_TIME_LIMIT = "task time limit reached"
KILL_SHUTDOWN = "dispatcher shutting down"
KILL_ROBOT_SERVICE_LOST = "stopped: the robot control service was lost"

NOT_DISPATCHED_PREFIX = "- rejected before running: "

# A failed verdict and detected faults, as the model reads them (dispatcher/docs/loop-and-context.md)
VERIFICATION_FAILED = "verification failed"  # the failure notice's {outcome} for an ok step
VERIFICATION_FAILED_DETAIL = "verification failed: {reasons}"
GOAL_NOT_REACHED = "the step did not reach its goal"  # no fault: the goal check failed
FAULT_TEXTS: dict[str, str] = {
    "fallen": "the robot fell",
    "fault_mode": "the robot is in a fault mode",
    "command_rejected": "the robot rejected the command",
}
