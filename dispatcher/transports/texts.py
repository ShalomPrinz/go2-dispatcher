"""Fixed texts of the CLI and Telegram transports (docs/running.md).

The model never reads them, so they are outside the prompt surface; they stay identical across conditions.
Telegram texts are HTML (parse_mode HTML); the emoji is the colour code (docs/running.md).
"""

from __future__ import annotations

__all__ = [
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
    "STEP_REASON_FAULTS",
    "STEP_REASON_GOAL",
    "STEP_REASON_WITH_FAULTS",
    "PROGRESS_THINKING",
    "PROGRESS_PLAN",
    "PROGRESS_PLAN_MESSAGE",
    "PROGRESS_PLAN_STEP",
    "PROGRESS_PLAN_DONE",
    "PROGRESS_PLAN_ABORT",
    "PROGRESS_PLAN_ASK",
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

# --- Replies (docs/running.md) -------------------------------------------------------------------------------

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
    "ASKED": "❓ {message}",  # the operator message already starts with "Question:"
    "STOPPED": "🛑 <b>Stopped</b> on request. A stop command was sent to the robot.",
    "TIME_LIMIT_EXCEEDED": "❌ <b>Stopped</b>: the task reached its time limit. A stop command was sent to the robot.",
    "FAILURE_BUDGET_EXHAUSTED": "❌ <b>Stopped</b>: the robot failed {n} times while trying this task.",
    "CALL_BUDGET_EXHAUSTED": "❌ <b>Stopped</b>: the task reached the limit of {n} planning calls without finishing.",
    "LLM_INVALID": "❌ <b>Stopped</b>: the model produced a malformed plan twice.",
    "LLM_ERROR": "❌ <b>Stopped</b>: the model could not be reached.",
    "STATE_UNAVAILABLE": "❌ <b>Stopped</b>: the robot state could not be read.",
    "PRECONDITION_FAILED": "❌ <b>Stopped</b>: a step was not possible in the robot's current posture.",
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
STEP_REASON_FAULTS = "failed: {faults}"  # an ok step with detected faults ({faults}: prompts.FAULT_TEXTS, joined)
STEP_REASON_GOAL = "did not reach its goal"  # an ok step whose goal check failed
STEP_REASON_WITH_FAULTS = "{reason}; {faults}"  # a failed step with detected faults
STEP_REASON_FALLBACK = "failed: {code}"  # an error code without a reason above, underscores as spaces


# --- Telegram progress message (docs/running.md) ------------------------------------------------------------

PROGRESS_THINKING = "🔵 <i>Thinking...</i>"
PROGRESS_PLAN = "ℹ️ <b>Plan</b>"
PROGRESS_PLAN_MESSAGE = "ℹ️ <b>Plan</b>: {message}"  # PLAN with its optional message
PROGRESS_PLAN_STEP = "{i}. {call}"
PROGRESS_PLAN_DONE = "✅ <b>Done</b>: {message}"
PROGRESS_PLAN_ABORT = "⚠️ <b>Aborted</b>: {message}"
PROGRESS_PLAN_ASK = "❓ <b>Question</b>: {message}"
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
