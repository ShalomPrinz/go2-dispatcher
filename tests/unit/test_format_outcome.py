"""format_outcome (docs/running.md)."""

from __future__ import annotations

import pytest

from dispatcher.models import StepResult, TaskOutcome
from dispatcher.registry import Registry
from dispatcher.transports import OUTCOME_MAX_CHARS, format_outcome
from helpers import REPO_ROOT


@pytest.fixture(scope="module")
def registry():
    return Registry.load(REPO_ROOT / "skills" / "catalog")


def step(index, outcome="ok", msg=None):
    return StepResult(index=index, call_index=1, plan_step=1, skill="sit", params={},
                      outcome=outcome, error_message=msg)


def outcome(steps, failures=0):
    return TaskOutcome(run_id="r", task="t", outcome="DONE", message="All done.", steps=steps,
                       llm_calls=1, failures=failures, duration_ms=1.0,
                       final_posture="standing", log_path="")


def test_basic(registry):
    text = format_outcome(outcome([step(1), step(None, "rejected", "bad"),
                                   step(2, "error", "boom")], failures=2), registry)
    assert text.splitlines() == [
        "DONE: All done.",
        "Steps: 2 run, 2 failed",
        "1. sit() -> ok",
        "- rejected before running: sit() -> rejected: bad",
        "2. sit() -> error: boom",
    ]


def test_no_steps(registry):
    assert format_outcome(outcome([]), registry) == "DONE: All done.\nSteps: 0 run, 0 failed"


def test_truncates_oldest_lines(registry):
    steps = [step(i, "error", "x" * 150) for i in range(1, 60)]
    text = format_outcome(outcome(steps, failures=59), registry)
    lines = text.splitlines()
    assert len(text) <= OUTCOME_MAX_CHARS
    m = lines[2]
    assert m.startswith("(") and m.endswith(" earlier lines omitted)")
    n = int(m[1:].split()[0])
    assert lines[3].startswith(f"{n + 1}. sit()")
    assert lines[-1].startswith("59. sit()")
