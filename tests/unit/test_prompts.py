"""prompts.py: operator messages, StopMove warning, notices, fixed texts (§11.8, §19.2)."""

from __future__ import annotations

import typing

import pytest

from go2_dispatcher import budget, prompts
from go2_dispatcher.models import TaskOutcomeCode

ALL_OUTCOMES = typing.get_args(TaskOutcomeCode)

OUTCOME_ARGS = {
    "DONE": {"message": "Walked 1 m."},
    "ABORTED": {"message": "Which chair?"},
    "STOPPED": {},
    "TIME_LIMIT_EXCEEDED": {"limit": 300.0},
    "FAILURE_BUDGET_EXHAUSTED": {"n": 3, "skill": "walk", "error_message": "Move returned 3104"},
    "CALL_BUDGET_EXHAUSTED": {"n": 20},
    "LLM_INVALID": {},
    "LLM_ERROR": {"detail": "overloaded"},
    "INTERNAL_ERROR": {"exception_type": "KeyError", "run_id": "abc123"},
}


def test_every_outcome_has_operator_message():
    assert set(prompts.OPERATOR_MESSAGES) == set(ALL_OUTCOMES)
    assert set(OUTCOME_ARGS) == set(ALL_OUTCOMES)
    for code in ALL_OUTCOMES:
        text = prompts.operator_message(code, **OUTCOME_ARGS[code])
        assert text and "{" not in text


def test_operator_message_wording():
    m = prompts.operator_message
    assert m("DONE", message="Walked {1} m.") == "Walked {1} m."
    assert m("TIME_LIMIT_EXCEEDED", limit=300.0) == (
        "Stopped: the task exceeded its 300s time limit. A stop command was sent to the robot.")
    assert m("FAILURE_BUDGET_EXHAUSTED", n=3, skill="walk", error_message="boom") == (
        "Stopped: the robot failed 3 times while trying this task. Last failure: walk: boom")
    assert m("CALL_BUDGET_EXHAUSTED", n=20) == (
        "Stopped: the task reached the limit of 20 planning calls without finishing.")
    assert m("LLM_ERROR", detail="HTTP 500") == "Stopped: the model could not be reached (HTTP 500)."
    assert m("INTERNAL_ERROR", exception_type="KeyError", run_id="r1") == (
        "Stopped: internal error (KeyError). See run log r1.")
    assert m("STOPPED") == "Stopped on request. A stop command was sent to the robot."


@pytest.mark.parametrize("code", ALL_OUTCOMES)
def test_stop_move_warning_appended(code):
    base = prompts.operator_message(code, **OUTCOME_ARGS[code])
    warned = prompts.operator_message(code, stop_move_failed=True, **OUTCOME_ARGS[code])
    assert warned == base + " WARNING: the stop command to the robot failed. Stop the robot manually."


def test_notices_format_with_arguments():
    assert prompts.notice("initial") == "(none)"
    assert prompts.notice("plan_complete").startswith("Your previous plan ran to completion.")
    assert prompts.notice("checkpoint", n=2) == (
        "You asked to review results after step 2 of your previous plan. Its remaining steps "
        "are listed under \"Remaining plan\"; include them again if you still want them.")
    assert prompts.notice("failure", n=2, skill="walk", outcome="error", f=1, max_failures=3) == (
        "Your previous plan failed at step 2 (walk): error. This is failure 1 of 3. Revise the "
        "plan to avoid that failure, or return ABORT with a message if the task cannot be done.")
    with pytest.raises(KeyError):
        prompts.notice("checkpoint")


def test_system_text_and_blocks():
    text = prompts.system_text(5)
    assert "A plan may contain at most 5 steps." in text
    assert "{" not in text
    assert prompts.system_blocks(5, "cat") == [text, "## Skills\ncat"]


def test_rejection_section():
    assert prompts.rejection_section(["a bad\nthing", "b"]) == (
        "## Your previous reply was rejected\na bad thing\nb\n"
        "Call submit_plan again with a corrected plan.")


def test_motion_budget_message_is_reexported():
    assert prompts.MOTION_BUDGET_MESSAGE is budget.MOTION_BUDGET_MESSAGE


def test_transport_texts():
    assert prompts.BUSY == 'Busy: a task is running. Send "stop" to stop it.'
    assert prompts.STOPPING == "Stopping."
    assert prompts.NOTHING_RUNNING == "Nothing is running."
    assert prompts.WORKING == "Working on it."
    assert prompts.EMPTY_TASK == "Send a task, for example: walk forward one metre."
    assert prompts.help_text("stub") == (
        'I control the Go2 robot. Send a task in plain words. Send "stop" to stop the current '
        'task. Backend: stub.')
