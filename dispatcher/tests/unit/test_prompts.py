"""prompts.py fixed texts as one golden file, plus completeness checks
(dispatcher/docs/loop-and-context.md, skills/docs/skills.md, tests/docs/testing.md)."""

from __future__ import annotations

import typing
from dataclasses import replace

import pytest

from dispatcher import prompts
from dispatcher.models import StepRef, StepResult, TaskOutcomeCode
from tests.helpers import REPO_ROOT

GOLDEN = REPO_ROOT / "dispatcher" / "tests" / "golden" / "fixed_texts.txt"
ALL_OUTCOMES = typing.get_args(TaskOutcomeCode)

FACTS = prompts.OutcomeFacts(
    run_id="abc123",
    time_limit_s=300.0,
    max_llm_calls=20,
    failures=3,
    last_failure=StepResult(
        ref=StepRef(call_index=1, plan_step=1, skill="walk", params={}),
        outcome="error",
        error_message="Move returned 3104",
    ),
    detail="overloaded",
    exception_type="KeyError",
)
OUTCOME_FACTS = {
    code: replace(FACTS, message={"DONE": "Walked 1 m.", "ABORTED": "Which chair?"}.get(code)) for code in ALL_OUTCOMES
}

NOTICE_ARGS = {
    "initial": {},
    "plan_complete": {},
    "checkpoint": {"n": 2},
    "failure": {"n": 2, "skill": "walk", "outcome": "error", "f": 1, "max_failures": 3},
}


def render_fixed_texts() -> str:
    """Every fixed text from prompts.py, rendered with example arguments and labelled."""
    parts: list[tuple[str, str]] = []
    for code in ALL_OUTCOMES:
        parts.append((f"operator_message {code}", prompts.operator_message(code, OUTCOME_FACTS[code])))
        parts.append(
            (
                f"operator_message {code} stop_move_failed",
                prompts.operator_message(code, OUTCOME_FACTS[code], stop_move_failed=True),
            )
        )
    for reason in prompts.NOTICES:
        parts.append((f"notice {reason}", prompts.notice(reason, **NOTICE_ARGS[reason])))
    parts.append(
        ("motion budget message", prompts.MOTION_BUDGET_MESSAGE.format(need=1.5, unit="m", kind="travel", left=0.5))
    )
    parts.append(("rejection_section", prompts.rejection_section(["a bad\nthing", "b"])))
    for name in ("BUSY", "STOPPING", "NOTHING_RUNNING", "WORKING", "EMPTY_TASK"):
        parts.append((name, getattr(prompts, name)))
    parts.append(('help_text("stub")', prompts.help_text("stub")))
    for i, block in enumerate(prompts.system_blocks(5, "<catalog>")):
        parts.append((f"system_blocks(5) block {i}", block))
    return "\n\n".join(f"=== {label} ===\n{text}" for label, text in parts) + "\n"


def test_fixed_texts_match_golden(update_golden):
    text = render_fixed_texts()
    if update_golden:
        GOLDEN.write_text(text, encoding="utf-8")
    assert text == GOLDEN.read_text(encoding="utf-8")


def test_every_outcome_has_operator_message():
    assert set(prompts.OPERATOR_MESSAGES) == set(ALL_OUTCOMES)
    assert set(NOTICE_ARGS) == set(prompts.NOTICES)
    for code in ALL_OUTCOMES:
        text = prompts.operator_message(code, OUTCOME_FACTS[code])
        assert text and "{" not in text


def test_notice_missing_arguments_raises():
    with pytest.raises(KeyError):
        prompts.notice("checkpoint")
